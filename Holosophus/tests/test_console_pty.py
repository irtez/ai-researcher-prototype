"""Exercise the actual entry point and prompt-toolkit in a PTY against HTTP APIs."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import socket
import struct
import subprocess
import sys
import termios
import threading
import time

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from console_client.terminal import clean_text

ROOT = Path(__file__).resolve().parents[1]


class TerminalProcess:
    def __init__(self, url, directory, *args):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 35, 140, 0, 0))
        self.process = subprocess.Popen([sys.executable, 'scripts/prototype_console.py', '--runner-url', url,
            '--data-url', url, '--state-dir', str(directory / 'state'), '--download-dir', str(directory / 'downloads'),
            '--poll-interval', '0.1', *args], cwd=ROOT, stdin=slave, stdout=slave, stderr=slave,
            env={**os.environ, 'TERM': 'xterm-256color', 'PROMPT_TOOLKIT_NO_CPR': '1'})
        os.close(slave)
        self.raw = b''; self.cursor = 0

    def expect(self, text, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            rendered = clean_text(self.raw.decode('utf-8', errors='replace'))
            pos = rendered.find(text, self.cursor)
            if pos >= 0:
                self.cursor = pos + len(text)
                return
            ready, _, _ = select.select([self.master], [], [], .1)
            if ready:
                try: chunk = os.read(self.master, 65536)
                except OSError: break
                if not chunk: break
                self.raw += chunk
        raise AssertionError(f'Expected {text!r}; terminal tail:\n{clean_text(self.raw.decode("utf-8", errors="replace"))[-4000:]}')

    def send(self, text=''):
        os.write(self.master, text.encode('utf-8') + b'\r')

    def reply(self, prompt, text=''):
        self.expect(prompt); self.send(text)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
        os.close(self.master)


def test_real_terminal_initial_approval_revision_detach_download_and_feedback(tmp_path):
    app = FastAPI()
    state = {'sid': None, 'iteration': 0, 'active': False, 'qid': None, 'stage': None, 'revised': False}
    starts = []; answers = []; uploads = []; cancellations = []

    @app.get('/ui/config')
    def catalog():
        return {'claude_code_default_model': 'deepseek', 'model_configs': {'claude_code': [
            {'id': 'deepseek', 'label': 'DeepSeek test', 'efforts': ['low', 'max'], 'default_effort': 'low'}]}}

    @app.post('/ui/sessions/{sid}/inputs')
    async def upload(sid: str, request: Request):
        form = await request.form(); batch = request.query_params.get('feedback_upload_id')
        uploads.append((sid, batch))
        return {'files': [{'path': f'inputs/{batch+"/" if batch else ""}{file.filename}'} for file in form.getlist('files')]}

    @app.post('/agents/manager')
    async def start(request: Request):
        body = await request.json(); starts.append(body)
        state.update(sid=body['session_id'], iteration=len(starts), active=True,
                     qid=f'sources-{len(starts)}', stage='sources', revised=False)
        return Response(content='{}\n', media_type='application/x-ndjson')

    @app.get('/agents/session/{sid}')
    def metadata(sid: str):
        if sid != state['sid']: return JSONResponse({'detail': 'Unknown'}, status_code=404)
        return {'active': state['active'], 'can_resume': not state['active'], 'status': 'running' if state['active'] else 'complete',
                'model_name': 'deepseek', 'model_label': 'DeepSeek test', 'effort': 'max', 'compute_mode': 'Local',
                'approvals': {'sources': True, 'plan': True}}

    @app.get('/ui/sessions/{sid}/iterations')
    def iterations(sid: str):
        return {'iterations': [{'id': f'{i:04d}'} for i in range(1, state['iteration']+1)],
                'active_iteration_id': f'{state["iteration"]:04d}' if state['active'] else None}

    @app.get('/ui/sessions/{sid}/iterations/{iid}')
    def iteration(sid: str, iid: str, after: int = 0):
        events = [{'sequence': 1, 'event_type': 'output', 'agent_name': 'manager', 'content': 'Прочитал входные таблицы'},
                  {'sequence': 2, 'event_type': 'agent_end', 'agent_name': 'mle_solver'}]
        return {'id': iid, 'status': 'running' if state['active'] else 'complete', 'events': [e for e in events if e['sequence'] > after],
                'next_after': 2, 'has_more': False, 'result_content': None if state['active'] else f'Итог расчёта {iid}: 46.069'}

    @app.get('/agents/question/{sid}')
    def question(sid: str):
        if not state['qid']: return Response(status_code=204)
        return {'qid': state['qid'], 'stage': state['stage'], 'question': 'Проверьте документ',
                'context': 'Исправленный план с единицами' if state['revised'] else 'Документ для рассмотрения', 'auto_approve_at': None}

    @app.post('/agents/answer')
    async def answer(request: Request):
        body = await request.json(); answers.append(body)
        assert body['qid'] == state['qid']
        if state['stage'] == 'sources':
            state.update(qid=f'plan-{state["iteration"]}', stage='plan')
        elif body['action'] == 'revise':
            state.update(qid=f'plan-revised-{state["iteration"]}', revised=True)
        else:
            state.update(active=False, qid=None)
        return {'status': 'ok'}

    @app.post('/agents/cancel')
    def cancel():
        cancellations.append(True); state.update(active=False, qid=None)
        return {'status': 'cancelled'}

    @app.get('/ui/sessions/{sid}/artifacts')
    def artifacts(sid: str):
        return {'artifacts': [{'path': 'results/table.csv', 'size_bytes': 13}]}

    @app.get('/ui/artifacts/{sid}/raw')
    def raw(sid: str, path: str):
        assert path == 'results/table.csv'
        return Response(content='mass\n46.069\n')

    listener = socket.socket(); listener.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True); thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(.02)
    file = tmp_path / 'input.csv'; file.write_text('name,smiles\nethanol,CCO\n')
    terminal = None
    try:
        terminal = TerminalProcess(url, tmp_path)
        terminal.reply('Главное меню', '1')
        terminal.reply('Профиль', '2')
        terminal.reply('Модель', '')
        terminal.reply('Уровень рассуждений', '2')
        terminal.reply('Вычисления', '1')
        terminal.reply('Согласования', '4')
        terminal.reply('Автосогласование', '0')
        terminal.reply('Задание. Несколько строк', 'Посчитать молекулярные массы')
        terminal.reply('>', '/send')
        terminal.reply('Вложения:', str(file))
        terminal.reply('1. input.csv', '')
        terminal.reply('Начать исследование?', '1')
        terminal.reply('1 — согласовать', '1')
        terminal.expect('СОГЛАСОВАНИЕ ПЛАНА')
        terminal.reply('1 — согласовать', '2')
        terminal.reply('Замечание. Несколько строк', 'Добавить единицы')
        terminal.reply('>', '/send')
        terminal.expect('Исправленный план с единицами')
        terminal.reply('1 — согласовать', '/exit')
        terminal.expect('Консоль отключена')
        assert terminal.process.wait(timeout=5) == 0
        assert state['active'] and not cancellations
        terminal.close()
        terminal = TerminalProcess(url, tmp_path, '--session', state['sid'])
        terminal.expect('Исправленный план с единицами')
        terminal.reply('1 — согласовать', '1')
        terminal.expect('Итог расчёта 0001')
        terminal.reply('Результат', '2')
        terminal.reply('Номер файла для скачивания', '1')
        terminal.expect('Файл сохранён:')
        assert (tmp_path / 'downloads' / state['sid'] / 'results/table.csv').read_text() == 'mass\n46.069\n'
        terminal.reply('Результат', '1')
        terminal.reply('Замечание к результату. Несколько строк', 'Округлить до двух знаков')
        terminal.reply('>', '/send')
        terminal.reply('Вложения:', str(file))
        terminal.reply('1. input.csv', '')
        terminal.reply('Согласования', '')
        terminal.reply('Автосогласование', '0')
        terminal.reply('Начать доработку?', '1')
        terminal.reply('1 — согласовать', '1')
        terminal.expect('СОГЛАСОВАНИЕ ПЛАНА')
        terminal.reply('1 — согласовать', '1')
        terminal.expect('Итог расчёта 0002')
        terminal.reply('Результат', '0')
        assert terminal.process.wait(timeout=5) == 0
        assert len(starts) == 2 and starts[1]['resume'] and starts[0]['session_id'] == starts[1]['session_id']
        assert starts[0]['effort'] == 'max' and starts[0]['research_profile'] == 'chemistry'
        assert starts[1]['feedback_upload_id'] == uploads[1][1]
        assert [a['action'] for a in answers] == ['approve', 'revise', 'approve', 'approve', 'approve']
        assert not cancellations
    finally:
        if terminal: terminal.close()
        server.should_exit = True; thread.join(timeout=5)

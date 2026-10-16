"""User-level console scenarios, independent of models and terminal capabilities."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from console_client.app import ConsoleApp, Detached
from console_client.terminal import EventRenderer, clean_text


class Terminal:
    def __init__(self, answers=()):
        self.answers = asyncio.Queue()
        for answer in answers: self.answers.put_nowait(answer)
        self.output = []
    async def ask(self, prompt, default=''):
        self.output.append(prompt)
        answer = await self.answers.get()
        if isinstance(answer, BaseException): raise answer
        return answer or default
    def write(self, text=''): self.output.append(str(text))


class API:
    def __init__(self):
        self.calls = []; self.current_question = None; self.complete = False
        self.stage = 0; self.fail_upload = False
    async def catalog(self):
        return {'claude_code_default_model': 'deepseek', 'model_configs': {'claude_code': [
            {'id': 'deepseek', 'label': 'DeepSeek', 'efforts': ['low', 'max'], 'default_effort': 'low'}]}}
    async def upload(self, sid, files, batch=None):
        self.calls.append(('upload', sid, files, batch))
        if self.fail_upload:
            self.fail_upload = False
            raise ValueError('Файл отклонён')
        return ['inputs/'+p.name for p in files]
    async def start(self, payload): self.calls.append(('start', payload))
    async def metadata(self, sid):
        return {'active': not self.complete, 'can_resume': self.complete, 'status': 'complete' if self.complete else 'running',
                'model_name': 'deepseek', 'model_label': 'DeepSeek', 'effort': 'low', 'compute_mode': 'Local',
                'approvals': {'sources': True, 'plan': True, 'auto_approve_after_seconds': None}}
    async def iterations(self, sid):
        return {'iterations': [{'id': '0001'}], 'active_iteration_id': None if self.complete else '0001'}
    async def iteration(self, sid, iid, after=0):
        events = [{'sequence': 1, 'agent_name': 'mle_solver', 'event_type': 'agent_end'}] if not after else []
        return {'id': iid, 'status': 'complete' if self.complete else 'running', 'events': events,
                'next_after': 1, 'has_more': False, 'result_content': 'Итоговая таблица' if self.complete else None}
    async def question(self, sid): return self.current_question
    async def answer(self, sid, qid, action, text=''):
        assert qid == self.current_question['qid']
        self.calls.append(('answer', qid, action, text))
        if action == 'revise':
            self.current_question = {'qid': 'q2', 'stage': 'plan', 'context': 'Исправленный план'}
        else:
            self.current_question = None; self.complete = True
    async def control(self, sid, verb, text=None, mode=None): self.calls.append(('control', verb, text, mode))
    async def cancel(self, sid): self.calls.append(('cancel', sid))


def test_task_and_files_preserved_on_upload_retry_and_feedback_uses_same_session(tmp_path):
    file = tmp_path / 'данные.csv'; file.write_text('x')
    async def scenario():
        api = API(); term = Terminal(['1'])
        app = ConsoleApp(api, term, poll_interval=.01)
        api.fail_upload = True
        settings = {'research_profile': 'chemistry', 'model_name': 'deepseek', 'effort': 'low', 'compute_mode': 'Local',
                    'approvals': {'sources': True, 'plan': True}}
        sid = await app.submit('Первая задача', [file], settings)
        assert sid.startswith('research_')
        assert len([c for c in api.calls if c[0] == 'start']) == 1
        initial = api.calls[-1][1]
        assert initial['input_files'] == ['inputs/данные.csv'] and not initial['resume']
        await app.submit('Уточнение', [file], settings, session_id=sid)
        feedback = api.calls[-1][1]
        assert feedback['session_id'] == sid and feedback['resume']
        assert feedback['feedback_upload_id'] == api.calls[-2][3]
        assert not {'model_name', 'compute_mode', 'research_profile'} & feedback.keys()
    asyncio.run(scenario())


def test_configuration_multiline_and_attachment_editing(tmp_path):
    file = tmp_path / 'input.csv'; file.write_text('x')
    async def scenario():
        term = Terminal(['2', '', '2', '1', '4', '0', 'Первая строка', 'Вторая строка', '/send', str(file), '/remove 1', str(file), ''])
        app = ConsoleApp(API(), term)
        settings = await app.configure()
        assert settings == {'research_profile': 'chemistry', 'model_name': 'deepseek', 'effort': 'max', 'compute_mode': 'Local',
                            'approvals': {'sources': True, 'plan': True, 'auto_approve_after_seconds': None}}
        assert await app.task_text() == 'Первая строка\nВторая строка'
        assert await app.attachments() == [file]
    asyncio.run(scenario())


def test_full_plan_revision_and_result_wait_for_server_completion():
    async def scenario():
        api = API(); term = Terminal(); app = ConsoleApp(api, term, poll_interval=.01)
        api.current_question = {'qid': 'q1', 'stage': 'plan', 'context': 'Исходный план'}
        task = asyncio.create_task(app.follow('s'))
        await asyncio.sleep(.04)
        assert not task.done()  # child agent_end in the first event page is insufficient
        term.answers.put_nowait('2'); term.answers.put_nowait('Добавить единицы'); term.answers.put_nowait('/send')
        await asyncio.sleep(.04)
        term.answers.put_nowait('1')
        result = await asyncio.wait_for(task, 2)
        assert result['status'] == 'complete'
        assert ('answer', 'q1', 'revise', 'Добавить единицы') in api.calls
        assert ('answer', 'q2', 'approve', '') in api.calls
        assert any('Исправленный план' in text for text in term.output)
        assert any('Итоговая таблица' in text for text in term.output)
    asyncio.run(scenario())


def test_stale_question_cancels_draft_and_exit_never_cancels_research():
    async def scenario():
        api = API(); term = Terminal(); app = ConsoleApp(api, term, poll_interval=.01)
        api.current_question = {'qid': 'old', 'stage': 'sources', 'context': 'Источники'}
        task = asyncio.create_task(app.follow('s'))
        await asyncio.sleep(.03)
        term.answers.put_nowait('2')
        await asyncio.sleep(.03)
        # Auto-approval/new question arrives while the operator types a comment.
        api.current_question = {'qid': 'new', 'stage': 'plan', 'context': 'Новый план'}
        await asyncio.sleep(.04)
        term.answers.put_nowait('/exit')
        with pytest.raises(Detached): await asyncio.wait_for(task, 2)
        assert not [c for c in api.calls if c[0] in ('answer', 'cancel')]
    asyncio.run(scenario())


def test_all_controls_and_ordinary_question():
    async def scenario():
        api = API(); term = Terminal(); app = ConsoleApp(api, term)
        for command in ('/pause', '/resume', '/steer Уточнить расчёт', '/stop', '/cancel'):
            await app.command('s', command)
        assert api.calls == [('control', 'pause', None, None), ('control', 'resume', None, None),
                             ('control', 'steer', 'Уточнить расчёт', 'next'), ('control', 'stop', None, None), ('cancel', 's')]
        api.current_question = {'qid': 'human', 'stage': None, 'question': 'Какие единицы?'}
        term.answers.put_nowait('мг/л'); term.answers.put_nowait('/send')
        await app.read_action('s', api.current_question)
        assert api.calls[-1] == ('answer', 'human', 'answer', 'мг/л')
    asyncio.run(scenario())


def test_messages_tools_and_terminal_escape_sequences_are_rendered_safely():
    term = Terminal(); render = EventRenderer(term)
    render.event({'agent_name': 'manager', 'activity': {'version': 1, 'kind': 'agent_start', 'invocationId': 'a', 'role': 'mle-solver', 'task': 'Расчёт'}})
    render.event({'activity': {'version': 1, 'kind': 'message', 'invocationId': 'a', 'text': 'Текст\nполностью'}})
    render.event({'activity': {'version': 1, 'kind': 'tool_call', 'invocationId': 'a', 'callId': 'tool', 'name': 'Bash', 'summary': 'Расчёт', 'input': {}}})
    render.event({'activity': {'version': 1, 'kind': 'tool_result', 'invocationId': 'a', 'callId': 'tool', 'output': 'x'*10000, 'isError': True}})
    assert 'Экспериментатор' in '\n'.join(term.output)
    assert any('Текст\nполностью' in text for text in term.output)
    assert any('ошибка' in text.lower() for text in term.output)
    assert max(map(len, term.output)) < 600
    assert clean_text('ok\x1b[2J\x1b]52;c;secret\x07!') == 'ok!'


def test_uncertain_feedback_cannot_report_old_result_as_new_success():
    from console_client.api import StartUncertain
    async def scenario():
        api = API(); api.complete = True
        async def lost(payload): raise StartUncertain('lost')
        api.start = lost
        app = ConsoleApp(api, Terminal())
        with pytest.raises(Detached):
            await app.submit('New feedback', [], {'approvals': {}}, session_id='old')
    asyncio.run(scenario())


def test_disappearing_attachment_can_be_removed_without_losing_dialog(tmp_path, monkeypatch):
    import console_client.app as module
    file = tmp_path / 'input.csv'; file.write_text('x')
    original = module.validate_files
    def vanished(paths):
        result = original(paths); file.unlink(); return result
    monkeypatch.setattr(module, 'validate_files', vanished)
    async def scenario():
        term = Terminal([str(file), '/remove 1', ''])
        assert await ConsoleApp(API(), term).attachments() == []
        assert any('недоступен' in text for text in term.output)
    asyncio.run(scenario())


def test_bearer_auth_requires_explicit_owner_and_reads_with_that_owner(tmp_path, monkeypatch):
    import prototype_console as entry
    from types import SimpleNamespace
    import httpx
    from console_client.api import ResearchAPI
    args = SimpleNamespace(client_id=None, state_dir=tmp_path, list=True,
                           runner_url='http://runner', data_url='http://data')
    monkeypatch.setenv('AI_RESEARCHER_CONSOLE_TOKEN', 'test-token')
    monkeypatch.setattr(entry, 'Terminal', Terminal)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['X-Holo-Client'] == 'alice'
        assert request.headers['Authorization'] == 'Bearer test-token'
        return httpx.Response(200, json={'sessions': []})
    monkeypatch.setattr(entry, 'ResearchAPI', lambda *a, **kw: ResearchAPI(*a, **kw, transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match='--client-id'):
        asyncio.run(entry.run(args))
    assert not calls
    args.client_id = 'alice'
    asyncio.run(entry.run(args))
    assert len(calls) == 1

"""Exercise the real UI with stale live caches and a controlled runner, without LLMs."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from fastapi.responses import FileResponse, Response
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'holosophos'), str(ROOT / 'codearkt')]
from holosophos.ui_api import create_app
from holosophos import research_iterations, research_session

workspace = Path(tempfile.mkdtemp(prefix='new-task-state-'))
work = workspace / 'research_finished'
marker = work / '.holosophus_ui'
marker.mkdir(parents=True)
(marker / 'owner').write_text('ui-test')
(marker / 'orchestrator').write_text('claude_code')
(work / 'started_prompt.md').write_text('Completed fixture')
item = research_iterations.start_iteration(work, kind='initial', user_message='Completed fixture', settings={})
(work / item['output_path']).write_text('# Готовый результат')
research_iterations.finish_iteration(work, item['id'], status='complete', result_path=item['output_path'])
research_session.save_run_status(work, 'complete')
state = {'active': False, 'status': 'complete', 'runner_available': True}
stale = {'sessionId': work.name, 'status': 'running', 'activeAgents': ['manager'],
         'events': [], 'rawPlan': '', 'transcriptByAgent': {}, 'observationCount': 0,
         'toolMarkers': [], 'startedAgents': ['manager'], 'endedAgents': []}
app = create_app(workspace_root=workspace)

@app.get('/agents/session/{sid}')
def metadata(sid: str):
    if not state['runner_available']:
        return Response(status_code=404)
    return {'session_id': sid, 'active': state['active'], 'status': state['status'],
            'can_resume': not state['active'], 'research_profile': 'general',
            'model_name': 'opus', 'effort': 'high', 'compute_mode': 'Local',
            'approvals': {'sources': False, 'plan': False}}

@app.get('/agents/question/{sid}')
def question(sid: str):
    return Response(status_code=204)

@app.get('/agents/events/{sid}')
def events(sid: str):
    return Response(status_code=404)

@app.post('/agents/manager')
def no_research():
    raise AssertionError('This test must never launch research')

@app.get('/{path:path}')
def frontend(path: str):
    if path.startswith('agents/'):
        return Response(status_code=404)
    file = ROOT / 'ui/dist' / path
    return FileResponse(file if file.is_file() else ROOT / 'ui/dist/index.html')

listener = socket.socket()
listener.bind(('127.0.0.1', 0))
base = f'http://127.0.0.1:{listener.getsockname()[1]}'
server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
thread.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(.05)
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('UI_TEST_CHROMIUM'), headless=True)
        cases = []
        for source, location in [('disk', '/sessions/'+work.name+'/run'), ('browser', '/')]:
            (marker / 'live_state.json').write_text(json.dumps(stale))
            context = browser.new_context()
            page = context.new_page()
            page.add_init_script("localStorage.setItem('holosophus.ui.clientId','ui-test')")
            if source == 'browser':
                page.add_init_script('localStorage.setItem("holosophus.ui.lastLiveSession",'+json.dumps(work.name)+');localStorage.setItem('+json.dumps('holosophus.ui.liveState.'+work.name)+','+json.dumps(json.dumps(stale))+');')
            page.goto(base+location)
            new = page.get_by_role('button', name='Новая задача', exact=True)
            expect(new).to_be_enabled(timeout=10000)
            new.click()
            prompt = page.locator('.task-launch-panel textarea')
            expect(prompt).to_be_enabled()
            prompt.fill('A new independent task')
            expect(page.locator('.task-launch-panel button[type=submit]')).to_be_enabled()
            # A reload on the landing page must not revive the same stale lock.
            page.reload()
            expect(page.locator('.task-launch-panel textarea')).to_be_enabled(timeout=10000)
            cases.append(source+' completed cache unlocks button and form')
            context.close()
        # Runner authority wins over a completed historical iteration/cache.
        context = browser.new_context();page = context.new_page()
        page.add_init_script("localStorage.setItem('holosophus.ui.clientId','ui-test')")
        page.goto(base+'/sessions/'+work.name+'/run')
        new = page.get_by_role('button', name='Новая задача', exact=True)
        expect(new).to_be_enabled(timeout=10000)
        for status in ['running', 'queued', 'paused', 'waiting_for_human', 'waiting_rate_limit']:
            state.update(active=True, status=status)
            expect(new).to_be_disabled(timeout=10000)
            state.update(active=False, status='complete')
            expect(new).to_be_enabled(timeout=10000)
        cases.append('active, queued, paused and waiting runs stay protected')
        state['runner_available'] = False
        page.reload()
        expect(new).to_be_enabled(timeout=10000)
        cases.append('server session status works without Claude runner metadata')
        browser.close();print(json.dumps({'passed': cases, 'llm_requests': 0}))
finally:
    server.should_exit = True
    thread.join(timeout=5)

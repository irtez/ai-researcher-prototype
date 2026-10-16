"""Console uses the existing HTTP contracts without SDK/model calls."""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from console_client.api import ResearchAPI, APIError, StartUncertain, validate_files


def test_upload_start_and_controls_keep_identity_and_approval_contract(tmp_path):
    calls = []
    file = tmp_path / 'данные с пробелом.csv'
    file.write_text('name,smiles\nethanol,CCO\n')

    def handler(req):
        calls.append(req)
        assert req.headers['X-Holo-Client'] == 'fixture'
        assert req.headers['X-Holo-Orchestrator'] == 'claude_code'
        if req.url.path.endswith('/inputs'):
            assert file.read_bytes() in req.content
            return httpx.Response(200, json={'files': [{'path': 'inputs/data.csv'}]})
        if req.url.path.endswith('/manager'):
            body = json.loads(req.content)
            assert body['approvals'] == {'sources': True, 'plan': True}
            assert body['input_files'] == ['inputs/data.csv']
            return httpx.Response(200, content=b'{"event_type":"agent_start"}\n')
        return httpx.Response(200, json={'status': 'ok'})

    async def scenario():
        async with ResearchAPI('http://runner', 'http://data', 'fixture', transport=httpx.MockTransport(handler)) as api:
            paths = await api.upload('research_fixed', [file])
            await api.start({'session_id': 'research_fixed', 'input_files': paths, 'approvals': {'sources': True, 'plan': True}})
            await api.answer('research_fixed', 'question-1', 'revise', 'Уточнить единицы')
            await api.control('research_fixed', 'steer', 'Округлить', 'next')
            await api.cancel('research_fixed')
    asyncio.run(scenario())
    assert json.loads(calls[-3].content) == {'session_id': 'research_fixed', 'qid': 'question-1', 'action': 'revise', 'text': 'Уточнить единицы'}
    assert json.loads(calls[-2].content)['mode'] == 'next'
    assert calls[-1].url.path == '/agents/cancel'


def test_files_are_all_checked_before_upload_and_rejected_upload_does_not_start(tmp_path):
    good = tmp_path / 'a.csv'; good.write_text('x\n1\n')
    requests = []
    def handler(req):
        requests.append(req.url.path)
        return httpx.Response(413, json={'detail': 'Too large'})
    async def scenario():
        async with ResearchAPI('http://runner', 'http://data', 'fixture', transport=httpx.MockTransport(handler)) as api:
            with pytest.raises(ValueError):
                await api.upload('s', [good, tmp_path / 'missing.csv'])
            assert not requests
            with pytest.raises(APIError, match='Too large'):
                await api.upload('s', [good])
    asyncio.run(scenario())
    assert requests == ['/ui/sessions/s/inputs']
    folder = tmp_path / 'another'; folder.mkdir()
    duplicate = folder / 'a.csv'; duplicate.write_text('x')
    with pytest.raises(ValueError, match='имена'):
        validate_files([good, duplicate])


def test_feedback_upload_batch_is_preserved_and_ambiguous_start_is_never_retried(tmp_path):
    file = tmp_path / 'input.csv'; file.write_text('x')
    starts = []
    def handler(req):
        if req.url.path.endswith('/inputs'):
            assert req.url.params['feedback_upload_id'] == 'batch-1'
            return httpx.Response(200, json={'files': [{'path': 'inputs/batch-1/input.csv'}]})
        starts.append(json.loads(req.content)['session_id'])
        raise httpx.ReadError('lost response')
    async def scenario():
        async with ResearchAPI('http://runner', 'http://data', 'fixture', transport=httpx.MockTransport(handler)) as api:
            await api.upload('same', [file], 'batch-1')
            with pytest.raises(StartUncertain):
                await api.start({'session_id': 'same', 'resume': True})
    asyncio.run(scenario())
    assert starts == ['same']


def test_download_keeps_paths_inside_directory_and_never_overwrites(tmp_path):
    async def scenario():
        async with ResearchAPI('http://runner', 'http://data', 'fixture', transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b'PDF'))) as api:
            target = await api.download('s', 'paper/paper.pdf', tmp_path)
            assert target == tmp_path / 'paper/paper.pdf'
            assert target.read_bytes() == b'PDF'
            with pytest.raises(FileExistsError):
                await api.download('s', 'paper/paper.pdf', tmp_path)
            for unsafe in ('../escape', '/absolute', 'x/../../escape', 'x\\escape'):
                with pytest.raises(ValueError):
                    await api.download('s', unsafe, tmp_path)
            (tmp_path / 'link').symlink_to(tmp_path.parent, target_is_directory=True)
            with pytest.raises(ValueError):
                await api.download('s', 'link/escape', tmp_path)
    asyncio.run(scenario())


def test_empty_question_and_http_errors_are_not_rendered_as_model_messages():
    async def scenario():
        def handler(req):
            return httpx.Response(204) if '/question/' in req.url.path else httpx.Response(401, json={'detail': 'Login required'})
        async with ResearchAPI('http://runner', 'http://data', 'fixture', transport=httpx.MockTransport(handler)) as api:
            assert await api.question('s') is None
            with pytest.raises(APIError) as error:
                await api.metadata('s')
            assert error.value.status == 401
    asyncio.run(scenario())


def test_broken_error_body_is_an_uncertain_start_not_a_retryable_failure():
    closed = []; calls = []
    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"detail":'
            raise httpx.ReadError('body disconnected')
        async def aclose(self): closed.append(True)
    def handler(req):
        calls.append(req)
        return httpx.Response(500, stream=BrokenBody())
    async def scenario():
        async with ResearchAPI('http://runner', 'http://data', 'fixture', transport=httpx.MockTransport(handler)) as api:
            with pytest.raises(StartUncertain):
                await api.start({'session_id': 'same', 'resume': True})
    asyncio.run(scenario())
    assert len(calls) == 1 and closed

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from console_client.monitor import SessionMonitor


class API:
    active = True
    iteration_id = '0001'
    status = 'running'
    events = [
        {'sequence': 1, 'event_type': 'agent_start', 'agent_name': 'manager'},
        {'sequence': 2, 'event_type': 'agent_end', 'agent_name': 'mle_solver'},
        {'sequence': 3, 'event_type': 'output', 'agent_name': 'manager', 'content': 'Continuing'},
    ]
    q = {'qid': 'first', 'stage': 'plan', 'context': 'Read the data'}

    async def metadata(self, sid):
        return {'active': self.active, 'status': self.status}

    async def iterations(self, sid):
        return {'active_iteration_id': self.iteration_id if self.active else None,
                'iterations': [{'id': self.iteration_id}]}

    async def iteration(self, sid, iid, after=0):
        page = [e for e in self.events if e['sequence'] > after][:2]
        return {'id': iid, 'events': page, 'next_after': page[-1]['sequence'] if page else after,
                'has_more': bool(page and page[-1]['sequence'] < len(self.events)), 'status': self.status,
                'result_content': 'The result' if self.status == 'complete' else None}

    async def question(self, sid):
        return self.q


def test_pagination_reconnect_and_child_end_are_not_run_completion():
    async def scenario():
        api = API(); monitor = SessionMonitor(api, 's')
        first = await monitor.poll()
        assert [e['sequence'] for e in first.events] == [1, 2, 3]
        assert not first.terminal
        assert not (await monitor.poll()).events
        # A failed poll must not consume new events or erase the cursor.
        original = api.question
        async def unavailable(sid): raise ConnectionError('offline')
        api.question = unavailable
        api.events = api.events + [{'sequence': 4, 'event_type': 'output', 'content': 'Last'}]
        try: await monitor.poll()
        except ConnectionError: pass
        else: raise AssertionError('transport error hidden')
        api.question = original
        assert [e['sequence'] for e in (await monitor.poll()).events] == [4]
        api.active = False; api.status = 'complete'; api.q = None
        done = await monitor.poll()
        assert done.terminal and done.iteration['result_content'] == 'The result'
        # Reattaching from a fresh process replays server-owned history.
        assert len((await SessionMonitor(api, 's').poll()).events) == 4
        api.active = True; api.status = 'running'; api.iteration_id = '0002'
        again = await monitor.poll()
        assert again.changed_iteration and len(again.events) == 4 and not again.terminal
    asyncio.run(scenario())


def test_completed_iteration_with_active_runner_is_not_done():
    async def scenario():
        api = API(); api.status = 'complete'
        assert not (await SessionMonitor(api, 's').poll()).terminal
    asyncio.run(scenario())


def test_pre_sdk_failure_is_read_from_durable_history_but_auth_errors_are_not():
    from console_client.api import APIError
    import pytest
    class EarlyFailure(API):
        status = 'error'
        active = False
        http_status = 404
        async def metadata(self, sid): raise APIError(self.http_status, 'Saved conversation is missing')
        async def question(self, sid): raise AssertionError('No pending question after terminal failure')
        async def iteration(self, *args):
            return {**(await super().iteration(*args)), 'error': 'Docker executor unavailable', 'settings': {'model_name': 'deepseek'}}
    async def scenario():
        api = EarlyFailure()
        snapshot = await SessionMonitor(api, 's').poll()
        assert snapshot.terminal and snapshot.iteration['error'] == 'Docker executor unavailable'
        assert snapshot.metadata['can_resume'] is False
        for status in (401, 403, 503):
            api.http_status = status
            with pytest.raises(APIError): await SessionMonitor(api, 's').poll()
        api.http_status = 404; api.status = 'running'; api.active = True
        with pytest.raises(APIError): await SessionMonitor(api, 's').poll()
    asyncio.run(scenario())

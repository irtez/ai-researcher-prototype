"""Cursor-based observation. Server state, not agent messages, owns completion."""
from dataclasses import dataclass

from .api import APIError


@dataclass
class Snapshot:
    metadata: dict
    iteration: dict
    events: list[dict]
    question: dict | None
    changed_iteration: bool

    @property
    def terminal(self):
        return not self.metadata.get('active', True) and self.iteration.get('status') in {'complete', 'error', 'cancelled', 'unknown'}


class SessionMonitor:
    def __init__(self, api, session_id: str):
        self.api, self.session_id = api, session_id
        self.iteration_id = None
        self.cursor = 0

    async def poll(self) -> Snapshot:
        metadata_error = None
        try:
            metadata = await self.api.metadata(self.session_id)
        except APIError as error:
            if error.status != 404:
                raise
            metadata_error = error
        history = await self.api.iterations(self.session_id)
        rows = history['iterations']
        iid = history.get('active_iteration_id') or (rows[-1]['id'] if rows else None)
        changed = iid != self.iteration_id
        cursor = 0 if changed else self.cursor
        events = []
        item = {}
        if iid:
            while True:
                item = await self.api.iteration(self.session_id, iid, cursor)
                events.extend(e for e in item['events'] if e['sequence'] > cursor)
                next_after = item['next_after']
                if item['has_more'] and next_after <= cursor:
                    raise ValueError('Сервер вернул повторяющуюся страницу истории.')
                cursor = next_after
                if not item['has_more']:
                    break
        if metadata_error:
            # Startup may fail before the SDK creates a resumable conversation.
            # Only accessible, terminal history confirms that failure, not a 404 alone.
            if history.get('active_iteration_id') or item.get('status') not in {'error', 'cancelled'}:
                raise metadata_error
            metadata = {**item.get('settings', {}), 'active': False,
                        'can_resume': False, 'status': item['status']}
            question = None
        else:
            question = await self.api.question(self.session_id)
        # Commit only after the whole snapshot succeeds; retries lose no events.
        self.iteration_id, self.cursor = iid, cursor
        return Snapshot(metadata, item, events, question, changed)

"""HTTP boundary only: no SDK, model keys or research logic in the console."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, suppress
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import httpx

EXTENSIONS = {'.csv', '.xls', '.xlsx', '.docx', '.json', '.jsonl', '.png', '.jpg', '.jpeg', '.pdf'}
MAX_INPUT_BYTES = 20 * 1024 * 1024


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f'HTTP {status}: {message}')


class StartUncertain(Exception):
    """The POST may have been accepted. Never silently create another session."""


def validate_files(paths: list[Path]) -> list[Path]:
    files = [Path(p).expanduser().resolve() for p in paths]
    names: set[str] = set()
    for path in files:
        if not path.is_file():
            raise ValueError(f'Файл не найден: {path}')
        if path.suffix.lower() not in EXTENSIONS:
            raise ValueError(f'Неподдерживаемый формат: {path.name}')
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f'Файл больше 20 Мбайт: {path.name}')
        if path.name.casefold() in names:
            raise ValueError('Вложения должны иметь разные имена файлов.')
        names.add(path.name.casefold())
    return files


def _base(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Адрес API должен быть HTTP(S) URL без пароля, query и fragment.')
    return url.rstrip('/')


class ResearchAPI:
    def __init__(self, runner_url: str, data_url: str, client_id: str, *, token: str | None = None, transport=None):
        self.runner_url, self.data_url = _base(runner_url), _base(data_url)
        headers = {'X-Holo-Client': client_id, 'X-Holo-Orchestrator': 'claude_code'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        self.client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30, connect=10), transport=transport)
        self._streams: set[asyncio.Task] = set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        for task in self._streams:
            task.cancel()
        await asyncio.gather(*self._streams, return_exceptions=True)
        await self.client.aclose()

    @staticmethod
    async def _check(response):
        if response.is_success:
            return
        await response.aread()
        try:
            detail = response.json().get('detail', response.reason_phrase)
        except (ValueError, AttributeError):
            detail = response.reason_phrase
        raise APIError(response.status_code, str(detail)[:800])

    async def _json(self, base, method, path, **kwargs):
        response = await self.client.request(method, base + path, **kwargs)
        await self._check(response)
        return None if response.status_code == 204 else response.json()

    async def catalog(self):
        return await self._json(self.data_url, 'GET', '/ui/config')

    async def sessions(self):
        return (await self._json(self.data_url, 'GET', '/ui/sessions', params={'limit': 200}))['sessions']

    async def metadata(self, sid):
        return await self._json(self.runner_url, 'GET', f'/agents/session/{quote(sid, safe="")}')

    async def iterations(self, sid):
        return await self._json(self.data_url, 'GET', f'/ui/sessions/{quote(sid, safe="")}/iterations')

    async def iteration(self, sid, iid, after=0):
        return await self._json(self.data_url, 'GET', f'/ui/sessions/{quote(sid, safe="")}/iterations/{quote(iid, safe="")}', params={'after': after, 'limit': 200})

    async def question(self, sid):
        return await self._json(self.runner_url, 'GET', f'/agents/question/{quote(sid, safe="")}')

    async def answer(self, sid, qid, action, text=''):
        return await self._json(self.runner_url, 'POST', '/agents/answer', json={'session_id': sid, 'qid': qid, 'action': action, 'text': text})

    async def control(self, sid, verb, text=None, mode=None):
        payload = {'session_id': sid, 'verb': verb}
        if text is not None:
            payload['text'] = text
        if mode is not None:
            payload['mode'] = mode
        return await self._json(self.runner_url, 'POST', '/agents/control', json=payload)

    async def cancel(self, sid):
        return await self._json(self.runner_url, 'POST', '/agents/cancel', json={'session_id': sid})

    async def upload(self, sid, paths: list[Path], feedback_upload_id=None):
        files = validate_files(paths)
        if not files:
            return []
        with ExitStack() as stack:
            attachments = [('files', (path.name, stack.enter_context(path.open('rb')), 'application/octet-stream')) for path in files]
            params = {'feedback_upload_id': feedback_upload_id} if feedback_upload_id else {}
            data = await self._json(self.data_url, 'POST', f'/ui/sessions/{quote(sid, safe="")}/inputs', files=attachments, params=params)
        return [file['path'] for file in data['files']]

    async def start(self, payload):
        payload = {**payload, 'orchestrator': 'claude_code', 'stream': True}
        request = self.client.build_request('POST', self.runner_url + '/agents/manager', json=payload, timeout=httpx.Timeout(None, connect=10))
        try:
            response = await self.client.send(request, stream=True)
        except httpx.TransportError as error:
            raise StartUncertain(f'Ответ запуска потерян. Сессия: {payload["session_id"]}') from error
        try:
            await self._check(response)
        except httpx.TransportError as error:
            with suppress(httpx.TransportError):
                await response.aclose()
            raise StartUncertain(f'Ответ запуска потерян. Сессия: {payload["session_id"]}') from error
        except APIError as error:
            with suppress(httpx.TransportError):
                await response.aclose()
            if error.status >= 500:
                raise StartUncertain(f'Сервер не подтвердил запуск. Сессия: {payload["session_id"]}') from error
            raise
        # Drain the ephemeral stream; render only the durable, paginated history.
        # Closing this client does not stop the independently running server task.
        async def drain():
            try:
                async for _ in response.aiter_bytes():
                    pass
            except httpx.TransportError:
                pass  # The monitor reconnects through durable history.
            finally:
                await response.aclose()
        task = asyncio.create_task(drain())
        self._streams.add(task)
        task.add_done_callback(self._streams.discard)

    async def artifacts(self, sid):
        return (await self._json(self.data_url, 'GET', f'/ui/sessions/{quote(sid, safe="")}/artifacts'))['artifacts']

    async def download(self, sid, path: str, directory: Path) -> Path:
        relative = PurePosixPath(path)
        if not relative.parts or relative.is_absolute() or '..' in relative.parts or '\\' in path:
            raise ValueError('Небезопасный путь файла.')
        directory = Path(directory).expanduser().resolve()
        target = directory.joinpath(*relative.parts)
        current = directory
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('Путь скачивания содержит символическую ссылку.')
        if target.exists():
            raise FileExistsError(f'Файл уже существует: {target}')
        async with self.client.stream('GET', self.data_url + f'/ui/artifacts/{quote(sid, safe="")}/raw', params={'path': path}) as response:
            await self._check(response)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                try:
                    async for chunk in response.aiter_bytes():
                        stream.write(chunk)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
        return target

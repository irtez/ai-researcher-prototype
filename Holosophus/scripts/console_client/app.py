"""Interactive workflow. Execution and approval decisions remain server-owned."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx

from .api import APIError, StartUncertain, validate_files
from .monitor import SessionMonitor
from .terminal import EventRenderer, STATUSES, clean_text

HELP = '/pause — пауза; /resume — продолжить; /steer текст — уточнение; /stop — остановить с сохранением; /cancel — немедленно остановить; /exit — отключиться'


class Detached(Exception):
    """Exit only the client. The server and active research are left running."""


class ConsoleApp:
    def __init__(self, api, terminal, *, poll_interval=1.0, download_dir=Path('console-downloads')):
        self.api, self.term = api, terminal
        self.poll_interval, self.download_dir = poll_interval, Path(download_dir)
        self.session_id = None

    async def ask(self, prompt, default=''):
        try:
            value = await self.term.ask(prompt, default)
        except (EOFError, KeyboardInterrupt) as error:
            raise Detached() from error
        if value.strip() == '/exit':
            raise Detached()
        return value.strip()

    async def choose(self, title, choices, default=0):
        self.term.write('\n' + title)
        numbered = {}
        for i, (_, label) in enumerate(choices, 1):
            key = '0' if choices[i - 1][0] == 'exit' else str(i)
            numbered[key] = choices[i - 1][0]
            self.term.write(f'{key}. {label}')
        while True:
            value = await self.ask('Номер', '0' if choices[default][0] == 'exit' else str(default + 1))
            if value in numbered:
                return numbered[value]
            self.term.write('Укажите номер из списка.')

    async def approvals(self, previous=None):
        previous = previous or {}
        default = int(bool(previous.get('sources'))) + 2 * int(bool(previous.get('plan')))
        mode = await self.choose('Согласования', [(0, 'Без согласований'), (1, 'Источники'), (2, 'План'), (3, 'Источники и план')], default)
        seconds = None
        if mode:
            while True:
                text = await self.ask('Автосогласование через N секунд (0 — только вручную)', str(previous.get('auto_approve_after_seconds') or 0))
                if text.isdigit():
                    seconds = int(text) or None
                    break
                self.term.write('Нужно целое число от 0.')
        return {'sources': bool(mode & 1), 'plan': bool(mode & 2), 'auto_approve_after_seconds': seconds}

    async def configure(self):
        catalog = await self.api.catalog()
        profile = await self.choose('Профиль', [('general', 'Компьютерные науки'), ('chemistry', 'Химия')])
        models = catalog['model_configs']['claude_code']
        if not models:
            raise ValueError('На сервере нет доступных моделей Claude Code.')
        default = next((i for i, m in enumerate(models) if m['id'] == catalog.get('claude_code_default_model')), 0)
        model_id = await self.choose('Модель', [(m['id'], m['label']) for m in models], default)
        model = next(m for m in models if m['id'] == model_id)
        effort = None
        efforts = model.get('efforts', [])
        if efforts:
            labels = {'low': 'Низкий', 'medium': 'Средний', 'high': 'Высокий', 'max': 'Максимальный'}
            default = efforts.index(model['default_effort']) if model.get('default_effort') in efforts else 0
            effort = await self.choose('Уровень рассуждений', [(e, labels.get(e, e)) for e in efforts], default)
        compute = await self.choose('Вычисления', [('Local', 'Локально'), ('Auto', 'Автоматически'), ('GPU', 'Удалённо (нужен настроенный узел)')])
        return {'research_profile': profile, 'model_name': model_id, 'effort': effort,
                'compute_mode': compute, 'approvals': await self.approvals()}

    async def task_text(self, title='Задание', *, session_id=None):
        self.term.write(f'\n{title}. Несколько строк; /send — отправить текст, /file путь — прочитать UTF-8 файл.')
        lines = []
        while True:
            line = await self.ask('>')
            if session_id and line.split(' ', 1)[0] in {'/pause', '/resume', '/steer', '/stop', '/cancel', '/help'}:
                await self.command(session_id, line)
            elif line == '/send':
                text = '\n'.join(lines).strip()
                if text:
                    return text
                self.term.write('Текст не должен быть пустым.')
            elif line.startswith('/file '):
                try:
                    text = Path(line[6:].strip().strip('"').strip("'")).expanduser().read_text(encoding='utf-8').strip()
                    if not text:
                        raise ValueError('Файл пуст.')
                    self.term.write(text)
                    return text
                except (OSError, UnicodeError, ValueError) as error:
                    self.term.write(f'Не удалось прочитать задание: {error}')
            else:
                lines.append(line)

    async def attachments(self):
        files = []
        self.term.write('\nВложения: путь к файлу, /remove N — удалить, Enter — закончить.')
        while True:
            for i, file in enumerate(files, 1):
                try:
                    detail = f'{file.stat().st_size} байт'
                except OSError:
                    detail = 'файл недоступен — восстановите его или удалите из списка'
                self.term.write(f'{i}. {file.name} ({detail})')
            text = await self.ask('Файл')
            if not text:
                return files
            try:
                if text.startswith('/remove '):
                    number = int(text.split(maxsplit=1)[1])
                    if not 1 <= number <= len(files):
                        raise ValueError('Нет такого номера.')
                    files.pop(number - 1)
                else:
                    files = validate_files([*files, Path(text.strip('"').strip("'"))])
            except (ValueError, OSError) as error:
                self.term.write(str(error))

    async def submit(self, text, files, settings, *, session_id=None):
        resume = session_id is not None
        sid = session_id or 'research_' + uuid4().hex
        self.session_id = sid
        batch = uuid4().hex if resume and files else None
        payload = {'session_id': sid, 'messages': [{'role': 'user', 'content': text}],
                   'resume': resume, 'approvals': settings['approvals']}
        if not resume:
            payload.update(settings)
        previous_ids = {row['id'] for row in (await self.api.iterations(sid))['iterations']} if resume else set()
        uploaded = False
        while True:
            try:
                if not uploaded:
                    paths = await self.api.upload(sid, files, batch)
                    payload['input_files'] = paths
                    if batch:
                        payload['feedback_upload_id'] = batch
                    uploaded = True
                await self.api.start(payload)
                self.term.write(f'\nСессия: {sid}')
                return sid
            except StartUncertain:
                self.term.write(f'Ответ запуска не получен. Проверяю сессию {sid}; повторно задание не отправляю.')
                try:
                    meta = await self.api.metadata(sid)
                    history = await self.api.iterations(sid)
                    if meta.get('active') or any(row['id'] not in previous_ids for row in history['iterations']):
                        return sid
                except (APIError, httpx.TransportError):
                    pass
                self.term.write(f'Новая итерация не подтверждена. Сохраните ID и подключитесь позже через --session {sid}.')
                raise Detached()
            except (APIError, httpx.TransportError, OSError, ValueError) as error:
                self.term.write(f'Отправка не выполнена: {error}')
                if await self.ask('1 — повторить с тем же заданием и файлами, 0 — отменить', '0') != '1':
                    return None

    async def command(self, sid, text):
        verb, _, rest = text.partition(' ')
        if verb == '/exit':
            raise Detached()
        if verb in ('/pause', '/resume', '/stop'):
            await self.api.control(sid, verb[1:])
        elif verb == '/cancel':
            await self.api.cancel(sid)
        elif verb == '/steer' and rest.strip():
            await self.api.control(sid, 'steer', rest.strip(), 'next')
        else:
            self.term.write(HELP)
            return
        self.term.write('Команда принята сервером.')

    async def read_action(self, sid, question):
        if question is None:
            await self.command(sid, await self.ask('Команда'))
            return
        qid = question['qid']  # Bound to the displayed document, never the next poll.
        if question.get('stage'):
            value = await self.ask('1 — согласовать, 2 — замечание, 3 — остановить; либо /команда')
            if value.startswith('/'):
                await self.command(sid, value)
            elif value == '1':
                await self.api.answer(sid, qid, 'approve')
            elif value == '2':
                await self.api.answer(sid, qid, 'revise', await self.task_text('Замечание', session_id=sid))
            elif value == '3':
                await self.api.cancel(sid)
            else:
                self.term.write('Выберите 1, 2 или 3.')
        else:
            await self.api.answer(sid, qid, 'answer', await self.task_text('Ответ менеджеру', session_id=sid))

    async def follow(self, sid):
        self.session_id = sid
        self.term.write(f'\nНаблюдение за сессией {sid}\n{HELP}')
        monitor, renderer = SessionMonitor(self.api, sid), EventRenderer(self.term)
        input_task = None
        shown_qid = None
        initialized = False
        status = None
        disconnected = False
        settings_shown = False

        async def poll(delay=0):
            if delay:
                await asyncio.sleep(delay)
            return await monitor.poll()

        poll_task = asyncio.create_task(poll())
        try:
            while True:
                pending = {poll_task}
                if input_task:
                    pending.add(input_task)
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if input_task in done:
                    try:
                        input_task.result()
                    except (APIError, httpx.TransportError, ValueError) as error:
                        self.term.write(f'Действие не выполнено: {error}')
                    input_task = None
                if poll_task not in done:
                    continue
                try:
                    snapshot = poll_task.result()
                except (APIError, httpx.TransportError, ValueError) as error:
                    if not disconnected:
                        self.term.write(f'Не удалось обновить состояние: {error}. Повторяю подключение; /exit — выйти.')
                    disconnected = True
                    if input_task is None:
                        input_task = asyncio.create_task(self.read_action(sid, None))
                    poll_task = asyncio.create_task(poll(self.poll_interval))
                    continue
                if disconnected:
                    self.term.write('Связь восстановлена.')
                    disconnected = False
                if not settings_shown:
                    meta = snapshot.metadata
                    self.term.write(f'Настройки выполнения: {meta.get("model_label") or meta.get("model_name", "")}, {meta.get("effort") or "без выбора effort"}, {meta.get("compute_mode", "")}')
                    settings_shown = True
                if snapshot.changed_iteration:
                    renderer = EventRenderer(self.term)
                    self.term.write(f'\nИтерация {snapshot.iteration.get("id", "")}')
                for event in snapshot.events:
                    renderer.event(event)
                new_status = snapshot.metadata.get('status')
                if new_status != status:
                    status = new_status
                    self.term.write('Состояние: ' + STATUSES.get(status, clean_text(status)))
                if snapshot.terminal:
                    self.term.write('\nИсследование: ' + STATUSES.get(snapshot.iteration['status'], 'состояние неизвестно'))
                    result = snapshot.iteration.get('result_content') or snapshot.iteration.get('answer')
                    if result:
                        self.term.write(result)
                    if snapshot.iteration.get('error'):
                        self.term.write('Причина: ' + snapshot.iteration['error'])
                    return snapshot.iteration
                question = snapshot.question
                qid = question and question['qid']
                if not initialized or qid != shown_qid:
                    if input_task:
                        input_task.cancel()
                        await asyncio.gather(input_task, return_exceptions=True)
                        input_task = None
                    if initialized and shown_qid:
                        self.term.write('Предыдущий запрос согласования закрыт или заменён.')
                    initialized, shown_qid = True, qid
                    if question:
                        title = {'sources': 'СОГЛАСОВАНИЕ ИСТОЧНИКОВ', 'plan': 'СОГЛАСОВАНИЕ ПЛАНА'}.get(question.get('stage'), 'ВОПРОС МЕНЕДЖЕРА')
                        self.term.write('\n' + title)
                        self.term.write(question.get('question', ''))
                        self.term.write(question.get('context', ''))
                        if question.get('auto_approve_at'):
                            at = datetime.fromtimestamp(question['auto_approve_at']).astimezone().strftime('%H:%M:%S %Z')
                            self.term.write('Автоматическое согласование в ' + at)
                if input_task is None:
                    input_task = asyncio.create_task(self.read_action(sid, question))
                poll_task = asyncio.create_task(poll(self.poll_interval))
        finally:
            tasks = [task for task in (poll_task, input_task) if task]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def files(self, sid):
        artifacts = await self.api.artifacts(sid)
        if not artifacts:
            self.term.write('Файлов пока нет.')
            return
        for i, artifact in enumerate(artifacts, 1):
            self.term.write(f'{i}. {artifact["path"]} ({artifact["size_bytes"]} байт)')
        value = await self.ask('Номер файла для скачивания; Enter — назад')
        if not value:
            return
        if not value.isdigit() or not 1 <= int(value) <= len(artifacts):
            self.term.write('Нет такого номера.')
            return
        artifact = artifacts[int(value) - 1]
        target = await self.api.download(sid, artifact['path'], self.download_dir / sid)
        self.term.write(f'Файл сохранён: {target}')

    async def new_task(self, prompt_file=None):
        settings = await self.configure()
        text = Path(prompt_file).expanduser().read_text(encoding='utf-8').strip() if prompt_file else await self.task_text()
        if not text:
            raise ValueError('Задание пусто.')
        files = await self.attachments()
        self.term.write('\nЗадание:\n' + text)
        self.term.write(f'Профиль: {settings["research_profile"]}; модель: {settings["model_name"]}; effort: {settings["effort"]}; вычисления: {settings["compute_mode"]}')
        self.term.write('Вложения: ' + (', '.join(p.name for p in files) or 'нет'))
        if await self.ask('Начать исследование? 1 — да, 0 — отмена', '1') != '1':
            return None
        return await self.submit(text, files, settings)

    async def run(self, *, session_id=None, prompt_file=None):
        self.term.write('AI Researcher — консольный прототип\nДля выхода в любом диалоге: /exit')
        sid = session_id
        if prompt_file:
            sid = await self.new_task(prompt_file)
        while True:
            try:
                if not sid:
                    mode = await self.choose('Главное меню', [('new', 'Новая задача'), ('sessions', 'Мои сессии'), ('exit', 'Выход')])
                    if mode == 'exit':
                        return
                    if mode == 'new':
                        sid = await self.new_task()
                    else:
                        sessions = await self.api.sessions()
                        if sessions:
                            sid = await self.choose('Сессии', [(s['session_id'], f'{s.get("title") or s["session_id"]} ({s["session_id"]})') for s in sessions])
                        else:
                            self.term.write('Сессии не найдены.')
                    if not sid:
                        continue
                await self.follow(sid)
                while sid:
                    action = await self.choose('Результат', [('feedback', 'Доработать результат'), ('files', 'Файлы'), ('new', 'Новая задача'), ('exit', 'Выход')])
                    if action == 'exit':
                        return
                    if action == 'new':
                        sid = None
                    elif action == 'files':
                        try:
                            await self.files(sid)
                        except (APIError, httpx.TransportError, OSError, ValueError) as error:
                            self.term.write(f'Не удалось получить файл: {error}')
                    else:
                        meta = await self.api.metadata(sid)
                        if not meta.get('can_resume'):
                            self.term.write('Доработка сейчас недоступна.')
                            continue
                        text = await self.task_text('Замечание к результату')
                        files = await self.attachments()
                        settings = {'approvals': await self.approvals(meta.get('approvals'))}
                        if await self.ask('Начать доработку? 1 — да, 0 — отмена', '1') == '1':
                            if await self.submit(text, files, settings, session_id=sid):
                                break
            except (APIError, httpx.TransportError, OSError, ValueError) as error:
                self.term.write(f'Ошибка: {error}')
                if await self.ask('1 — повторить, 0 — главное меню', '0') != '1':
                    sid = None

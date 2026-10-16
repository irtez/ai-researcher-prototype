#!/usr/bin/env python3
"""AI Researcher console prototype: a local client of the shared research server."""
import argparse
import asyncio
import os
from pathlib import Path
from uuid import uuid4

import httpx
from prompt_toolkit.patch_stdout import patch_stdout

from console_client.api import ResearchAPI, APIError
from console_client.app import ConsoleApp, Detached
from console_client.terminal import Terminal, clean_text


def client_identity(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / 'client-id'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = path.read_text().strip()
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in value):
            raise ValueError('Некорректный client-id в каталоге состояния консоли.')
        return value
    value = 'console_' + uuid4().hex
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
    return value


async def run(args):
    token = os.environ.get('AI_RESEARCHER_CONSOLE_TOKEN')
    if token and not args.client_id:
        raise ValueError('При авторизации укажите --client-id с именем пользователя, которому выдан токен.')
    owner = args.client_id or client_identity(args.state_dir)
    term = Terminal()
    async with ResearchAPI(args.runner_url, args.data_url, owner, token=token) as api:
        if args.list:
            for item in await api.sessions():
                term.write(f'{item["session_id"]}  {item.get("title", "")}')
            return
        app = ConsoleApp(api, term, poll_interval=args.poll_interval, download_dir=args.download_dir)
        try:
            await app.run(session_id=args.session, prompt_file=args.prompt_file)
        except Detached:
            term.write('\nКонсоль отключена. Активное исследование продолжает работу на сервере.')
            if app.session_id:
                term.write('Повторное подключение: --session ' + app.session_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner-url', default=os.environ.get('CONSOLE_RUNNER_URL', 'http://127.0.0.1:5061'))
    parser.add_argument('--data-url', default=os.environ.get('CONSOLE_DATA_URL', 'http://127.0.0.1:5065'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--session', help='Подключиться к существующей сессии')
    mode.add_argument('--prompt-file', type=Path, help='Новое задание из файла UTF-8')
    mode.add_argument('--list', action='store_true', help='Показать свои сессии и выйти')
    parser.add_argument('--client-id', help='Явный владелец сессий (обычно не требуется)')
    parser.add_argument('--state-dir', type=Path, default=Path('.console-state'))
    parser.add_argument('--download-dir', type=Path, default=Path('console-downloads'))
    parser.add_argument('--poll-interval', type=float, default=1.0)
    args = parser.parse_args()
    if not .1 <= args.poll_interval <= 60:
        parser.error('--poll-interval должен быть от 0.1 до 60 секунд')
    try:
        with patch_stdout():
            asyncio.run(run(args))
    except KeyboardInterrupt:
        print('\nКонсоль отключена; исследование не остановлено.')
    except (APIError, httpx.TransportError, OSError, ValueError) as error:
        print(clean_text(f'Не удалось запустить консоль: {error}'))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

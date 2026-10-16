"""Linear terminal display; external content is always plain, sanitized text."""
import re
import unicodedata

from prompt_toolkit import PromptSession

ROLES = {'manager': 'Менеджер', 'librarian': 'Поиск литературы', 'proposer': 'Гипотезы',
         'mle-solver': 'Экспериментатор', 'mle-solver-remote': 'Удалённый экспериментатор',
         'writer': 'Автор статьи', 'reviewer': 'Рецензент'}
STATUSES = {'queued': 'в очереди', 'running': 'выполняется', 'paused': 'пауза',
            'waiting_for_human': 'ожидает ответа', 'waiting_rate_limit': 'ожидает доступности модели',
            'complete': 'завершено', 'error': 'ошибка', 'cancelled': 'остановлено',
            'limited': 'достигнут лимит', 'unknown': 'состояние не подтверждено'}


def clean_text(value) -> str:
    text = str(value)
    text = re.sub(r'\x1b\][\s\S]*?(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    return ''.join(c for c in text if c in '\n\t' or not unicodedata.category(c).startswith('C'))


class Terminal:
    def __init__(self):
        self.prompt = PromptSession()

    async def ask(self, prompt: str, default: str = '') -> str:
        suffix = f' [{default}]' if default else ''
        value = await self.prompt.prompt_async(clean_text(prompt) + suffix + ': ')
        return value.strip() or default

    def write(self, text=''):
        print(clean_text(text), flush=True)


class EventRenderer:
    def __init__(self, terminal):
        self.terminal = terminal
        self.roles = {}
        self.tools = {}

    def event(self, event):
        activity = event.get('activity') or {}
        kind = activity.get('kind')
        iid = activity.get('invocationId')
        role = self.roles.get(iid, event.get('agent_name') or 'manager').replace('_', '-')
        label = ROLES.get(role, role)
        if activity.get('version') == 1:
            if kind == 'agent_start':
                role = activity.get('role', role).replace('_', '-')
                self.roles[iid] = role
                self.terminal.write(f'\n[{ROLES.get(role, role)}] Начал работу. {clean_text(activity.get("task", ""))[:200]}')
            elif kind == 'agent_resume':
                self.terminal.write(f'\n[{label}] Продолжает работу')
            elif kind == 'agent_end':
                self.terminal.write(f'[{label}] {STATUSES.get(activity.get("status"), "Работа агента закончилась")}')
                if activity.get('summary'):
                    self.terminal.write(activity['summary'])
            elif kind == 'message':
                self.terminal.write(f'\n[{label}]\n{clean_text(activity.get("text", ""))}')
            elif kind == 'tool_call':
                name = activity.get('name', 'Инструмент')
                self.tools[activity.get('callId')] = name
                self.terminal.write(f'[{label} → {name}] {clean_text(activity.get("summary", ""))[:180]}')
            elif kind == 'tool_result':
                name = self.tools.get(activity.get('callId'), 'Инструмент')
                error = activity.get('isError') is True
                self.terminal.write(f'[{label} → {name}] ' + ('Ошибка: ' + clean_text(activity.get('output', ''))[:300] if error else 'Выполнено'))
            return
        event_type = event.get('event_type', '')
        if event_type == 'output' and event.get('content'):
            self.terminal.write(f'\n[{label}]\n{clean_text(event["content"])}')
        elif event_type == 'agent_start':
            self.terminal.write(f'[{label}] Начал работу')
        elif event_type == 'agent_end':
            self.terminal.write(f'[{label}] Работа агента закончилась')
        elif event_type == 'agent_interrupted':
            self.terminal.write(f'[{label}] Прервано')

import { useEffect } from "react";
import type { ReactNode } from "react";

// The application UI is Russian-only, independent of browser/stored preferences.
// Keep the dictionary shape so old translation entries need no unrelated rewrite.
export function getLang(): "ru" {
  return "ru";
}

type Entry = { ru: string; en: string };

// Flat dictionary keyed by dotted namespaces. `{name}` placeholders are filled
// by `t(key, vars)`. Enum-style values (statuses, event types, ...) live under a
// prefix and are resolved with `tv(prefix, value)`, which falls back to the raw
// value for anything not listed.
const DICT: Record<string, Entry> = {
  "files.title": { ru: "Файлы", en: "Files" },
  "files.tree": { ru: "Папки и файлы сессии", en: "Session folders and files" },
  "files.documents": { ru: "Документы Markdown", en: "Markdown documents" },
  "files.noMatches": { ru: "По этому запросу файлов нет.", en: "No files match this search." },
  "files.more": { ru: "Показать ещё {n} (осталось {remaining})", en: "Show {n} more ({remaining} remaining)" },
  "files.inputs": { ru: "Входные файлы", en: "Inputs" },
  "files.results": { ru: "Результаты", en: "Results" },
  "files.sources": { ru: "Источники", en: "Sources" },
  "files.plans": { ru: "Планы", en: "Plans" },
  "files.experiments": { ru: "Эксперименты", en: "Experiments" },
  "files.code": { ru: "Код", en: "Code" },
  "files.tables": { ru: "Данные и результаты расчётов", en: "Data and calculation results" },
  "files.summaries": { ru: "Сводки экспериментатора", en: "Experiment summaries" },
  "files.figures": { ru: "Иллюстрации", en: "Figures" },
  "files.paper": { ru: "Статья", en: "Paper" },
  "activity.feed": { ru: "Ход работы", en: "Activity" },
  "activity.waitingForManager": { ru: "Задача принята. Ожидается первый ответ менеджера…", en: "Task accepted. Waiting for the manager’s first response…" },
  "approval.confirmSources": { ru: "Согласовать источники?", en: "Approve sources?" },
  "approval.confirmPlan": { ru: "Согласовать план?", en: "Approve plan?" },
  "approval.pendingSources": { ru: "Ожидает согласования: источники", en: "Awaiting approval: sources" },
  "approval.pendingPlan": { ru: "Ожидает согласования: план", en: "Awaiting approval: plan" },
  "approval.pendingQuestion": { ru: "Агент ожидает вашего ответа", en: "An agent is waiting for your reply" },
  "approval.goToQuestion": { ru: "Перейти", en: "Go to request" },
  "activity.agent": { ru: "Агент", en: "Agent" },
  "activity.task": { ru: "Поручение менеджера", en: "Manager’s assignment" },
  "activity.agentAnswer": { ru: "Ответ агента", en: "Agent response" },
  "activity.unknownStatus": { ru: "статус не сохранён", en: "status not recorded" },
  "activity.unknownTool": { ru: "Инструмент", en: "Tool" },
  "activity.savedEvent": { ru: "Сохранённое событие", en: "Recorded event" },
  "activity.roleHistory": { ru: "История роли", en: "Role history" },
  "activity.legacy": { ru: "В этой записи нет точных связей между вызовами. Старые сообщения сгруппированы по роли; повторные вызовы роли не разделены, ответы инструментов не восстановлены предположениями.", en: "This record lacks exact call relationships. Older messages are grouped by role; repeated invocations are not separated and tool responses are not inferred." },
  "activity.missingStart": { ru: "Начало этого вызова не сохранено в доступной истории.", en: "The start of this invocation is missing from the available history." },
  "activity.missingCall": { ru: "Ответ сохранён, но запись вызова с названием и параметрами отсутствует.", en: "The response was saved, but its call name and arguments are missing." },
  "activity.missingResponse": { ru: "Ответ инструмента в истории не сохранён.", en: "The tool response was not saved in history." },
  "activity.truncated": { ru: "Длинный текст сокращён при сохранении в журнал; бинарные вложения здесь не отображаются.", en: "Long text was shortened in the log; binary attachments are not displayed here." },
  "activity.views": { ru: "Работа агентов", en: "Agent activity" },
  "activity.openAgent": { ru: "Открыть работу агента", en: "Open agent activity" },
  "activity.back": { ru: "К менеджеру", en: "Back to manager" },
  "activity.noEvents": { ru: "Событий пока нет.", en: "No events yet." },
  "activity.manager": { ru: "Менеджер", en: "Manager" },
  "activity.background": { ru: "работает в фоне", en: "running in background" },
  "activity.started": { ru: "Агент начал работу…", en: "Agent started…" },
  "activity.arguments": { ru: "Параметры", en: "Arguments" },
  "activity.response": { ru: "Ответ инструмента", en: "Tool response" },
  "activity.emptyResponse": { ru: "Инструмент завершился без текста ответа.", en: "Tool completed without a text response." },
  "activity.awaitingResponse": { ru: "Ожидается ответ инструмента…", en: "Waiting for tool response…" },
  "feedback.collapse": { ru: "Свернуть", en: "Collapse" },
  "history.earlierEvents": { ru: "Показать предыдущие события", en: "Show earlier events" },
  "history.select": { ru: "Итерация", en: "Iteration" },
  "history.initial": { ru: "Исходная задача", en: "Initial task" },
  "history.feedback": { ru: "Доработка {n}", en: "Revision {n}" },
  "history.legacy": { ru: "Прежняя сессия", en: "Earlier session" },
  "history.follow": { ru: "Текущая работа", en: "Current work" },
  "history.latest": { ru: "Последний успешный результат", en: "Latest successful result" },
  "history.details": { ru: "История и результат", en: "History and result" },
  "history.result": { ru: "Результат", en: "Result" },
  "history.legacyNote": { ru: "Сохранённые данные прежней сессии; границы сообщений и этапов могут отсутствовать.", en: "Saved data from an earlier session; message and stage boundaries may be unavailable." },
  "history.noResult": { ru: "У этой итерации пока нет готового результата.", en: "This iteration has no published result." },
  "history.requestAndPlans": { ru: "Задача, планы и решения", en: "Request, plans and decisions" },
  "history.remark": { ru: "Замечание пользователя", en: "User feedback" },
  "history.request": { ru: "Задача пользователя", en: "User request" },
  "history.notSaved": { ru: "Не сохранено", en: "Not saved" },
  "history.plan": { ru: "План", en: "Plan" },
  "history.currentPlan": { ru: "План этой итерации", en: "This iteration’s plan" },
  "history.sources": { ru: "Источники", en: "Sources" },
  "history.sourcesFrom": { ru: "Без изменений из «{iteration}»", en: "Unchanged from “{iteration}”" },
  "history.approvalLog": { ru: "История согласований", en: "Approval history" },
  "history.planDocuments": { ru: "Сохранённые документы планирования", en: "Saved planning documents" },
  "history.reusedSources": { ru: "сохранено согласование прежних источников", en: "previous source approval reused" },
  "history.answer": { ru: "Итоговое сообщение менеджера", en: "Manager\u2019s final message" },
  "history.artifactsNote": { ru: "Вкладки и список файлов содержат рабочие артефакты всей сессии. Доработка продолжает последний успешный результат.", en: "Tabs and file lists contain working artifacts from the whole session. Feedback continues from the latest successful result." },
  "history.loadError": { ru: "Не удалось обновить историю итераций.", en: "Could not refresh iteration history." },
  "approvalStage.question": { ru: "Вопрос", en: "Question" },
  "decision.pending": { ru: "ожидает согласования", en: "awaiting approval" },
  "resolution.manual": { ru: "вручную", en: "manual" },
  "resolution.auto": { ru: "по таймеру", en: "timer" },
  "resolution.operator": { ru: "оператор", en: "operator" },
  "runStatus.unknown": { ru: "статус неизвестен", en: "status unknown" },
  "stageStatus.not_run": { ru: "не выполнялся", en: "not run" },
  "stageStatus.pending": { ru: "ещё не запускался", en: "not started yet" },
  "stageStatus.running": { ru: "выполняется", en: "running" },
  "stageStatus.complete": { ru: "завершён", en: "completed" },
  "stageStatus.limited": { ru: "достигнут лимит, работа не завершена", en: "limit reached, work incomplete" },
  "stageStatus.unknown": { ru: "состояние неизвестно", en: "status unknown" },
  "stageStatus.error": { ru: "ошибка", en: "error" },
  "stageStatus.cancelled": { ru: "прерван", en: "interrupted" },

  "common.none": { ru: "нет", en: "none" },
  "common.na": { ru: "н/д", en: "n/a" },
  "common.loading": { ru: "загрузка", en: "loading" },
  "common.noRows": { ru: "Строк нет", en: "No rows" },

  "settings.title": { ru: "Настройки", en: "Settings" },
  "settings.language": { ru: "Язык", en: "Language" },

  "topbar.sessionPrefix": { ru: "Сессия", en: "Session" },
  "topbar.modelUnknown": { ru: "модель неизвестна", en: "model unknown" },
  "topbar.orchestratorTitle": { ru: "Оркестратор запуска", en: "Run orchestrator" },
  "topbar.collapseMeta": { ru: "Свернуть строку статуса", en: "Collapse the status strip" },
  "topbar.expandMeta": { ru: "Развернуть строку статуса", en: "Expand the status strip" },
  "topbar.loggedInTitle": { ru: "Вы вошли в систему", en: "You are logged in" },
  "topbar.signedInAs": { ru: "Вход: {name}", en: "Signed in: {name}" },
  "topbar.logout": { ru: "Выйти", en: "Log out" },
  "topbar.loginToRun": { ru: "Войти, чтобы запускать исследования", en: "Log in to start research" },
  "topbar.login": { ru: "Войти", en: "Log in" },
  "topbar.stopTitle": { ru: "Остановить запуск", en: "Stop the run" },
  "topbar.stop": { ru: "Стоп", en: "Stop" },

  "queue.queued": { ru: "В очереди", en: "Queued" },
  "queue.queuedPosOf": { ru: "В очереди — позиция {pos} из {depth}", en: "Queued — position {pos} of {depth}" },
  "queue.queuedPos": { ru: "В очереди — позиция {pos}", en: "Queued — position {pos}" },

  "capacity.title": { ru: "Ёмкость запусков", en: "Run capacity" },
  "capacity.loading": { ru: "Ёмкость…", en: "Capacity…" },
  "gpu.title": { ru: "Использование GPU", en: "GPU usage" },
  "gpu.openTitle": { ru: "Показать загрузку видеокарт", en: "Show GPU usage" },
  "gpu.local": { ru: "Локально", en: "Local" },
  "gpu.remote": { ru: "Удалённо", en: "Remote" },
  "gpu.card": { ru: "Карта", en: "Card" },
  "gpu.memory": { ru: "Память", en: "Memory" },
  "gpu.util": { ru: "Загрузка", en: "Util" },
  "gpu.stateFree": { ru: "свободна", en: "free" },
  "gpu.stateBusy": { ru: "занята", en: "busy" },
  "gpu.stateOff": { ru: "вне пула", en: "not in pool" },
  "gpu.heldBy": { ru: "Занята сессией {session}", en: "Held by session {session}" },
  "gpu.notConnected": { ru: "Нет подключения к удалённому хосту", en: "Not connected to the remote host" },
  "gpu.disabled": { ru: "Локальный GPU выключен", en: "Local GPU is disabled" },
  "gpu.noData": { ru: "Нет данных", en: "No data" },
  "gpu.freeOf": { ru: "свободно {free} из {total}", en: "{free} of {total} free" },
  "gpu.noneFree": { ru: "Свободных карт нет — запуск на GPU сейчас не пройдёт", en: "No free cards — a GPU run will not start right now" },
  "gpu.pool": { ru: "Пул карт (удалённый хост)", en: "Card pool (remote host)" },
  "gpu.poolHint": { ru: "Holosophus берёт карты только из этого набора", en: "Holosophus leases only from this set" },
  "gpu.poolSaved": { ru: "Сохранено", en: "Saved" },
  "gpu.poolError": { ru: "Не удалось сохранить", en: "Could not save" },
  "gpu.updated": { ru: "обновлено {sec} с назад", en: "updated {sec}s ago" },
  "gpu.temp": { ru: "Темп.", en: "Temp" },
  "gpu.power": { ru: "Питание", en: "Power" },
  "gpu.processes": { ru: "Процессы", en: "Processes" },
  "gpu.noProcesses": { ru: "процессов нет", en: "no processes" },
  "gpu.pin": { ru: "Занять эту карту вручную", en: "Pin this card manually" },
  "gpu.pinned": { ru: "Закреплена вручную", en: "Pinned manually" },
  "gpu.unpin": { ru: "Снять закрепление", en: "Clear the pin" },
  "gpu.pinNotice": {
    ru: "Карта #{index} закреплена вручную: запуск займёт её, даже если она занята. Автовыбор отключён.",
    en: "Card #{index} is pinned by hand: a run will take it even while it is busy. Automatic selection is off."
  },
  "capacity.slots": { ru: "Слоты", en: "Slots" },
  "capacity.queue": { ru: "очередь", en: "queue" },
  "capacity.yours": { ru: "твои", en: "yours" },
  "capacity.load": { ru: "нагрузка", en: "load" },
  "capacity.slotsTitle": {
    ru: "Слоты запусков: {active}/{limit}, в очереди: {queued}.",
    en: "Run slots: {active}/{limit}, queued: {queued}."
  },
  "capacity.yoursTitle": { ru: "Ваши запуски: {active} из {limit}.", en: "Your runs: {active} of {limit}." },
  "capacity.yoursTitleNoLimit": { ru: "Ваши запуски: {active}.", en: "Your runs: {active}." },
  "capacity.hostLoad": {
    ru: "Нагрузка хоста: load1 {load1} на {cpu} CPU, RAM {mem}.",
    en: "Host load: load1 {load1} on {cpu} CPU, RAM {mem}."
  },
  "capacity.waiting": { ru: "ждут {n}", en: "waiting {n}" },
  "capacity.unavailable": { ru: "{label}: недоступен (лимит не задан).", en: "{label}: unavailable (no limit set)." },
  "capacity.busyOf": {
    ru: "{label}: занято {busy} из {slots} (свободно {free})",
    en: "{label}: {busy} of {slots} busy (free {free})"
  },
  "capacity.localExecutor": { ru: "Локальный executor", en: "Local executor" },

  "sidebar.sessions": { ru: "Логи", en: "Logs" },
  "sidebar.delete": { ru: "Удалить лог", en: "Delete log" },
  "sidebar.deleteConfirm": { ru: "Удалить безвозвратно?", en: "Delete permanently?" },
  "sidebar.deleteYes": { ru: "Удалить", en: "Delete" },
  "sidebar.deleteNo": { ru: "Отмена", en: "Cancel" },
  "sidebar.deleteHint": {
    ru: "Файлы запуска будут удалены. Трейсы Langfuse не затрагиваются.",
    en: "The run's files are removed. Langfuse traces are not affected."
  },
  "sidebar.deleteRunning": { ru: "Нельзя удалить активный запуск", en: "Cannot delete an active run" },
  "sidebar.deleteFailed": { ru: "Не удалось удалить", en: "Delete failed" },
  "sidebar.expand": { ru: "Развернуть список сессий", en: "Expand session list" },
  "sidebar.collapse": { ru: "Свернуть список сессий", en: "Collapse session list" },
  "sidebar.newTask": { ru: "Новая задача", en: "New task" },
  "sidebar.searchSessions": { ru: "Поиск сессий", en: "Search sessions" },
  "sidebar.loading": { ru: "Загрузка сессий", en: "Loading sessions" },
  "sidebar.loadError": { ru: "Не удалось загрузить сессии", en: "Failed to load sessions" },
  "sidebar.noSessions": { ru: "Сессии не найдены", en: "No sessions found" },
  "sidebar.artifacts": { ru: "артефакты", en: "artifacts" },

  "launch.title": { ru: "Новая исследовательская задача", en: "New research task" },
  "launch.profileAria": { ru: "Профиль исследования", en: "Research profile" },
  "launch.profilePrefix": { ru: "Профиль", en: "Profile" },
  "launch.placeholder": {
    ru: "Опишите, что нужно исследовать, рассчитать или сравнить.\nУкажите исходные данные и приложенные файлы, ограничения и ожидаемый результат.\nПри необходимости уточните, нужны ли поиск литературы, эксперименты, статья и рецензия.",
    en: "Describe the task, constraints, expected artifacts, and success criteria"
  },
  "launch.modeAria": { ru: "Режим запуска", en: "Run mode" },
  "launch.modeResearch": { ru: "Исследование", en: "Research" },
  "launch.modeDebug": { ru: "Отладка", en: "Debug" },
  "launch.executorAria": { ru: "Вычисления", en: "Compute" },
  "launch.executorTitle": { ru: "Где выполнять вычислительные эксперименты", en: "Where to run computations" },
  "launch.executorAuto": { ru: "Вычисления: автоматически", en: "Compute: automatic" },
  "launch.executorLocal": { ru: "Вычисления: локально", en: "Compute: local" },
  "launch.executorGpu": { ru: "Вычисления: удалённо", en: "Compute: remote" },
  // Hardware-named variants: filled from live telemetry so the label names the
  // card the run will actually get, instead of an abstract "local"/"remote".
  "launch.executorLocalNamed": { ru: "Вычисления: локально · {device}", en: "Compute: local · {device}" },
  "launch.executorGpuNamed": { ru: "Вычисления: удалённо · {device}", en: "Compute: remote · {device}" },
  "launch.computeHint.Auto": { ru: "Менеджер выберет подходящего исполнителя из доступных вариантов.", en: "The manager chooses an available executor." },
  "launch.computeHint.Local": { ru: "Расчёты выполняются в Docker на сервере AI Researcher. Доступ к GPU определяется настройками сервера.", en: "Computations run in Docker on the AI Researcher server." },
  "launch.computeHint.GPU": { ru: "Расчёты выполняются на удалённом узле, настроенном оператором. GPU используется при его наличии и настройке.", en: "Computations run on a configured remote host." },
  "launch.promptGuide": { ru: "Цель → исходные данные → ограничения → ожидаемый результат", en: "Goal → inputs → constraints → result" },
  "launch.effort": { ru: "Уровень рассуждений", en: "Reasoning effort" },
  "effort.none": { ru: "Без дополнительного рассуждения", en: "None" },
  "effort.minimal": { ru: "Минимальный", en: "Minimal" },
  "effort.low": { ru: "Низкий", en: "Low" },
  "effort.medium": { ru: "Средний", en: "Medium" },
  "effort.high": { ru: "Высокий", en: "High" },
  "effort.xhigh": { ru: "Очень высокий", en: "Extra high" },
  "effort.max": { ru: "Максимальный", en: "Maximum" },
  "launch.modelAria": { ru: "Модель", en: "Model" },
  "launch.modelTitle": { ru: "Языковая модель для этого запуска", en: "Which LLM this run uses" },
  "launch.modelAuto": { ru: "Модель: Авто", en: "Model: Auto" },
  "launch.modelPrefix": { ru: "Модель", en: "Model" },
  "launch.modelDefault": { ru: "По умолчанию ({model})", en: "Default ({model})" },
  "launch.modelSummary": { ru: "Модель: {model}", en: "Model: {model}" },
  "launch.orchestratorAria": { ru: "Оркестратор", en: "Orchestrator" },
  "launch.orchestratorTitle": { ru: "Какой движок оркестрации запускает задачу", en: "Which orchestration engine runs the task" },
  "launch.settings": { ru: "Настройки запуска", en: "Run settings" },
  "launch.attachFiles": { ru: "Прикрепить файлы", en: "Attach files" },
  "feedback.newInputsApproval": { ru: "Новые файлы потребуют повторного согласования источников.", en: "New files will require sources approval again." },
  "launch.attachedFiles": { ru: "Прикреплённые файлы", en: "Attached files" },
  "launch.removeFile": { ru: "Убрать файл {name}", en: "Remove file {name}" },
  "launch.uploading": { ru: "Загрузка файлов…", en: "Uploading files…" },
  "launch.starting": { ru: "Запуск…", en: "Starting…" },
  "launch.uploadFailed": { ru: "Файлы не загружены: {message}", en: "Files were not uploaded: {message}" },
  "launch.start": { ru: "Старт", en: "Start" },

  "approval.settingsTitle": { ru: "Согласования", en: "Approvals" },
  "approval.sourcesSwitch": { ru: "Согласовать выбранные источники", en: "Approve selected sources" },
  "approval.planSwitch": { ru: "Согласовать план исследования", en: "Approve the research plan" },
  "approval.autoSwitch": { ru: "Согласовать автоматически через", en: "Approve automatically after" },
  "approval.secondsAria": { ru: "Время до автоматического согласования", en: "Time until automatic approval" },
  "approval.secondsSuffix": { ru: "с", en: "sec" },
  "approval.autoExplanation": {
    ru: "Если корректировка не отправлена вовремя, выбранный этап будет согласован автоматически.",
    en: "If no correction is sent in time, the selected stage is approved automatically."
  },
  "approval.invalidTimeout": {
    ru: "Укажите целое число секунд больше нуля и выберите хотя бы один этап.",
    en: "Enter a positive whole number of seconds and select at least one stage."
  },
  "approval.sourcesTitle": { ru: "Проверка источников", en: "Source review" },
  "approval.planTitle": { ru: "Проверка плана", en: "Plan review" },
  "approval.questionTitle": { ru: "Нужен ваш ответ", en: "Your answer is needed" },
  "approval.correctionPlaceholder": { ru: "Опишите необходимую корректировку", en: "Describe the required correction" },
  "approval.answerPlaceholder": { ru: "Введите ответ", en: "Enter your answer" },
  "approval.approve": { ru: "Согласовать", en: "Approve" },
  "approval.sendCorrection": { ru: "Отправить корректировку", en: "Send correction" },
  "approval.answer": { ru: "Ответить", en: "Answer" },
  "approval.autoCountdown": {
    ru: "Автоматическое согласование через {seconds} с, если корректировка не будет отправлена.",
    en: "Automatic approval in {seconds} sec unless you send a correction."
  },
  "approval.autoPending": {
    ru: "Срок автоматического согласования наступил; ожидается обновление статуса.",
    en: "The automatic approval deadline has passed; waiting for the status to update."
  },
  "approval.stale": {
    ru: "Этот запрос уже изменился или был обработан. Показаны актуальные данные.",
    en: "This request changed or was already handled. The current state has been refreshed."
  },
  "approval.latestDecision": { ru: "Последнее решение:", en: "Latest decision:" },

  "decision.approved": { ru: "согласовано вручную", en: "approved manually" },
  "decision.auto_approved": { ru: "согласовано автоматически", en: "approved automatically" },
  "decision.changes_requested": { ru: "отправлена корректировка", en: "correction sent" },
  "decision.answered": { ru: "ответ отправлен", en: "answer sent" },
  "decision.cancelled": { ru: "запрос отменён", en: "request cancelled" },
  "decision.invalidated": { ru: "требуется повторное согласование", en: "approval is required again" },
  "decision.timeout": { ru: "ответ не получен вовремя", en: "answer timed out" },
  "approvalStage.sources": { ru: "источники", en: "sources" },
  "approvalStage.plan": { ru: "план", en: "plan" },

  "operator.pause": { ru: "Пауза", en: "Pause" },
  "operator.resume": { ru: "Продолжить", en: "Resume" },
  "operator.stopCheckpoint": { ru: "Остановить с сохранением", en: "Stop and save" },
  "operator.stopTitle": {
    ru: "Сохранить текущее состояние и завершить запуск",
    en: "Save the current state and finish the run"
  },
  "operator.steerPlaceholder": { ru: "Уточните ход исследования", en: "Adjust the research direction" },
  "operator.next": { ru: "после текущего шага", en: "after current step" },
  "operator.now": { ru: "сейчас", en: "now" },
  "operator.steer": { ru: "Направить", en: "Steer" },
  "operator.queued": { ru: "Запуск в очереди", en: "Run queued" },
  "operator.rateLimit": { ru: "Ожидание доступности модели", en: "Waiting for model availability" },

  "feedback.title": { ru: "Доработка результата", en: "Revise the result" },
  "feedback.placeholder": {
    ru: "Опишите, что нужно уточнить, исправить или дополнить",
    en: "Describe what to clarify, correct, or add"
  },
  "feedback.submit": { ru: "Доработать", en: "Revise" },
  "feedback.alreadyActive": { ru: "Для этой сессии уже выполняется запуск", en: "This session already has an active run" },
  "feedback.failed": { ru: "Не удалось начать доработку", en: "Could not start the revision" },
  "feedback.savedConfig": {
    ru: "Продолжение с прежними настройками: {profile}, {model}, {compute}.",
    en: "Continues with the saved settings: {profile}, {model}, {compute}."
  },
  "feedback.unavailableLegacy": {
    ru: "Доработка недоступна: у этой сессии нет сохранённого состояния для продолжения.",
    en: "Revision is unavailable because this session has no saved state to continue from."
  },
  "feedback.unavailableState": {
    ru: "Для этой сессии пока нет состояния, из которого можно продолжить доработку.",
    en: "This session does not currently have a saved state that can be revised."
  },
  "feedback.metadataError": {
    ru: "Не удалось проверить возможность доработки. Повторите попытку после обновления.",
    en: "Could not check whether this session can be revised. Try again after refreshing."
  },

  "profile.general": { ru: "Компьютерные науки", en: "Computer science" },
  "profile.chemistry": { ru: "Химия", en: "Chemistry" },
  "profileDescription.general": { ru: "Поиск научной информации, анализ данных и вычислительные эксперименты.", en: "Literature, data analysis and computational experiments." },
  "profileDescription.chemistry": { ru: "Анализ лабораторных данных, поиск соединений и расчёт молекулярных свойств.", en: "Laboratory data, compounds and molecular properties." },

  "orchestrator.codearkt": { ru: "Оркестратор: Стандартный", en: "Orchestrator: Standard" },
  "orchestrator.claude_code": { ru: "Оркестратор: Claude Code", en: "Orchestrator: Claude Code" },

  "tab.brief": { ru: "Литература", en: "Literature" },
  "tab.proposals": { ru: "Гипотезы", en: "Proposals" },
  "tab.experiments": { ru: "Эксперименты", en: "Experiments" },
  "tab.paper": { ru: "Статья", en: "Paper" },
  "tab.review": { ru: "Рецензия", en: "Review" },
  "tab.run": { ru: "Детали запуска", en: "Run details" },

  "notebook.loading": { ru: "Загрузка данных блокнота", en: "Loading notebook data" },
  "notebook.loadError": { ru: "Не удалось загрузить данные блокнота", en: "Failed to load notebook data" },

  "launch.preparingSession": { ru: "Подготовка сессии…", en: "Preparing session…" },
  "brief.title": { ru: "Литературный обзор", en: "Literature overview" },
  "brief.empty": { ru: "Литературных материалов пока нет.", en: "No literature materials yet." },
  "brief.meta": { ru: "статьи: {papers} - источники: {sources}", en: "papers: {papers} — sources: {sources}" },
  "brief.originalPrompt": { ru: "Исходный запрос", en: "Original prompt" },

  "proposals.title": { ru: "Доска гипотез", en: "Proposal board" },
  "proposals.meta": { ru: "артефакты гипотез: {count}", en: "proposal artifacts: {count}" },
  "proposals.item": { ru: "Гипотеза {index}", en: "Proposal {index}" },

  "experiments.title": { ru: "Эксперименты", en: "Experiments" },
  "experiments.meta": { ru: "таблицы результатов: {metrics} · сводки: {summaries}", en: "result tables: {metrics} · summaries: {summaries}" },
  "experiments.summary": { ru: "Сводка экспериментатора", en: "Experimenter summary" },
  "experiments.summaryError": { ru: "Не удалось прочитать сводку целиком. Подробности доступны в исходном файле.", en: "Could not read the full summary. See the source file for details." },
  "experiments.legacyLog": { ru: "Прежний журнал экспериментатора (архив)", en: "Earlier experimenter log (archive)" },

  "paper.title": { ru: "Статья", en: "Paper" },
  "paper.openPdf": { ru: "Открыть PDF", en: "Open PDF" },
  "paper.missingPdf": { ru: "PDF статьи ещё не сформирован. Другие PDF доступны в файлах исследования.", en: "The paper PDF has not been built yet. Other PDFs are available in the research files." },
  "paper.expectedFile": { ru: "Ожидаемый файл: {path}", en: "Expected file: {path}" },
  "paper.registrationError": { ru: "Не удалось прочитать сведения о статье: они повреждены или недоступны.", en: "The paper registration is invalid or unavailable." },
  "paper.status.checking": { ru: "Проверка статьи выполняется.", en: "The paper check is in progress." },
  "paper.status.ready": { ru: "Проверка статьи завершена.", en: "The paper check is complete." },
  "paper.changedSinceCheck": { ru: "После проверки статья была изменена. Показана последняя версия PDF.", en: "The paper was changed after its check. The latest PDF is shown." },
  "paper.status.blocked": { ru: "При проверке статьи обнаружены замечания.", en: "The paper check reported issues." },
  "paper.status.abandoned": { ru: "Проверка статьи не завершена.", en: "The paper check was not completed." },
  "paper.latestPdf": { ru: "Последняя собранная версия PDF. Наличие файла не означает, что рецензирование завершено.", en: "Latest built PDF. This file does not indicate that review is complete." },
  "paper.figures": { ru: "Изображения", en: "Images" },
  "experiments.figures": { ru: "Графики и изображения", en: "Plots and images" },
  "markdown.image": { ru: "Изображение", en: "Image" },

  "review.title": { ru: "Рецензия", en: "Review" },
  "review.meta": { ru: "Оценка {score} - {rec}", en: "Score {score} — {rec}" },
  "review.pending": { ru: "ожидается", en: "pending" },
  "review.noDecision": { ru: "решения нет", en: "no decision" },
  "review.strengths": { ru: "Сильные стороны", en: "Strengths" },
  "review.weaknesses": { ru: "Слабые места", en: "Weaknesses" },
  "review.requiredFixes": { ru: "Что исправить", en: "Required fixes" },
  "review.confidence": { ru: "Уверенность рецензента: {score}", en: "Reviewer confidence: {score}" },
  "review.questions": { ru: "Вопросы рецензента", en: "Reviewer questions" },
  "review.limitations": { ru: "Ограничения", en: "Limitations" },
  "review.formatIssues": { ru: "Замечания к оформлению", en: "Formatting issues" },
  "review.noItems": { ru: "Пунктов нет", en: "No items" },

  "run.title": { ru: "Детали запуска", en: "Run details" },
  "run.meta": { ru: "артефакты запуска: {artifacts} - экспорты трасс: {traces}", en: "run artifacts: {artifacts} — trace exports: {traces}" },
  "run.liveEvents": { ru: " - live-события: {n}", en: " — live events: {n}" },
  "run.langfuseExports": { ru: "Экспорты Langfuse", en: "Langfuse exports" },
  "run.traceExport": { ru: "экспорт трассы", en: "trace export" },

  "queued.banner": {
    ru: "{label} — слот освободится, и запуск продолжится автоматически.",
    en: "{label} — a slot will free up and the run continues automatically."
  },

  "live.eventFeed": { ru: "Лента событий", en: "Event feed" },
  "live.agentPlan": { ru: "План агента", en: "Agent plan" },
  "live.noEvents": { ru: "Событий пока нет", en: "No events yet" },

  "tool.callAria": { ru: "Вызов инструмента {index}", en: "Tool call {index}" },
  "tool.call": { ru: "Вызов инструмента", en: "Tool call" },
  "tool.result": { ru: "Результат", en: "Result" },
  "tool.noResult": { ru: "Результат пока не получен", en: "No result yet" },
  "tool.unattached": { ru: "Непривязанные результаты", en: "Unattached results" },

  "transcript.plan": { ru: "План", en: "Plan" },
  "transcript.final": { ru: "Финальный ответ", en: "Final answer" },

  "eventType.agent_end": { ru: "агент завершил работу", en: "agent finished" },
  "eventType.agent_interrupted": { ru: "запуск остановлен", en: "run stopped" },
  "eventType.agent_start": { ru: "агент начал работу", en: "agent started" },
  "eventType.observation": { ru: "результат инструмента", en: "tool result" },
  "eventType.output": { ru: "ответ", en: "response" },
  "eventType.planning_output": { ru: "план", en: "plan" },
  "eventType.tool_results": { ru: "результаты инструментов", en: "tool results" },

  "runPanel.run": { ru: "Запуск", en: "Run" },
  "runPanel.currentAgent": { ru: "Текущий агент", en: "Current agent" },
  "runPanel.waitingSlot": { ru: "ожидает слот", en: "waiting for slot" },

  "drawer.allArtifacts": { ru: "Все артефакты", en: "All artifacts" },
  "drawer.searchArtifacts": { ru: "Поиск артефактов", en: "Search artifacts" },
  "drawer.allTypes": { ru: "Все типы", en: "All types" },
  "drawer.artifacts": { ru: "Артефакты", en: "Artifacts" },

  "preview.close": { ru: "Закрыть предпросмотр", en: "Close preview" },
  "preview.loading": { ru: "Загрузка предпросмотра", en: "Loading preview" },
  "preview.loadError": { ru: "Не удалось загрузить предпросмотр", en: "Failed to load preview" },
  "preview.download": { ru: "Скачать", en: "Download" },
  "preview.binary": { ru: "Бинарный артефакт", en: "Binary artifact" },

  "login.title": { ru: "Вход", en: "Sign in" },
  "login.subtitle": { ru: "Войдите, чтобы запускать исследования", en: "Sign in to start research" },
  "login.close": { ru: "Закрыть", en: "Close" },
  "login.loginLabel": { ru: "Логин", en: "Login" },
  "login.passwordLabel": { ru: "Пароль", en: "Password" },
  "login.failed": { ru: "Не удалось войти", en: "Sign-in failed" },
  "login.submit": { ru: "Войти", en: "Sign in" },
  "login.required": { ru: "Требуется вход", en: "Login required" },

  "copy.copied": { ru: "Скопировано", en: "Copied" },
  "copy.copy": { ru: "Скопировать", en: "Copy" },

  "source.lines": { ru: "строк: {n}", en: "lines: {n}" },
  "source.codeLabel": { ru: "код", en: "code" },

  "json.root": { ru: "корень", en: "root" },
  "json.array": { ru: "Массив({n})", en: "Array({n})" },
  "json.object": { ru: "Объект({n})", en: "Object({n})" },

  "artifacts.emptyTab": { ru: "Для этой вкладки артефактов нет", en: "No artifacts for this tab" },
  "workbench.noSessions": { ru: "Сохранённых сессий нет", en: "No sessions in the workspace" },
  "mode.workspace": { ru: "рабочие файлы", en: "workspace" },
  "taskType.workspace": { ru: "рабочие файлы", en: "workspace" },

  "mode.debug": { ru: "отладка", en: "debug" },
  "mode.research": { ru: "исследование", en: "research" },
  "mode.smoke": { ru: "проверка", en: "check" },

  "taskType.debug": { ru: "отладка", en: "debug" },
  "taskType.full_paper": { ru: "полная статья", en: "full paper" },
  "taskType.proposal": { ru: "гипотезы", en: "proposals" },
  "taskType.research": { ru: "исследование", en: "research" },
  "taskType.smoke": { ru: "проверка", en: "check" },

  "runStatus.cancelled": { ru: "отменено", en: "cancelled" },
  "runStatus.complete": { ru: "завершено", en: "completed" },
  "runStatus.error": { ru: "ошибка", en: "error" },
  "runStatus.idle": { ru: "нет запуска", en: "no run" },
  "runStatus.queued": { ru: "в очереди", en: "queued" },
  "runStatus.running": { ru: "выполняется", en: "running" },
  "runStatus.paused": { ru: "на паузе", en: "paused" },
  "runStatus.waiting_for_human": { ru: "ждёт ответа", en: "waiting for you" },
  "runStatus.waiting_rate_limit": { ru: "ждёт лимит", en: "waiting (rate limit)" },

  "stage.Experiments": { ru: "Эксперименты", en: "Experiments" },
  "stage.Literature overview": { ru: "Литературный обзор", en: "Literature overview" },
  "stage.Paper": { ru: "Статья", en: "Paper" },
  "stage.Prompt": { ru: "Запрос", en: "Prompt" },
  "stage.Proposals": { ru: "Гипотезы", en: "Proposals" },
  "stage.Review": { ru: "Рецензия", en: "Review" },

  "previewKind.csv": { ru: "CSV", en: "CSV" },
  "previewKind.image": { ru: "изображение", en: "image" },
  "previewKind.json": { ru: "JSON", en: "JSON" },
  "previewKind.jsonl": { ru: "JSONL", en: "JSONL" },
  "previewKind.markdown": { ru: "Markdown", en: "Markdown" },
  "previewKind.pdf": { ru: "PDF", en: "PDF" },
  "previewKind.text": { ru: "текст", en: "text" }
};

function interpolate(text: string, vars?: Record<string, string | number>): string {
  if (!vars) {
    return text;
  }
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in vars ? String(vars[name]) : match));
}

// Translate a dotted key, filling `{name}` placeholders. Unknown keys fall back
// to the key itself (surfaces missing translations without crashing).
export function t(key: string, vars?: Record<string, string | number>): string {
  const entry = DICT[key];
  return interpolate(entry ? entry.ru : key, vars);
}

// Translate an enum-style value under a prefix (e.g. tv("runStatus", "running")).
// Unknown values fall back to the raw value, matching the old label-map behaviour.
export function tv(prefix: string, value: string): string {
  const entry = DICT[`${prefix}.${value}`];
  return entry ? entry.ru : value;
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    document.documentElement.lang = "ru";
  }, []);
  return <>{children}</>;
}

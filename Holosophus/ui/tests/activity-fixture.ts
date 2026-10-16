import type { ActivityEvent } from "../src/activity/activity";

// Deliberately synthetic; values illustrate the already-discussed smoke scenario.
// No API calls, session writes or model requests are made by the preview.
const raw: Array<Omit<ActivityEvent, "id" | "at"> & Record<string, unknown>> = [
  { kind: "message", text: "Сравню этанол и уксусную кислоту. Экспериментатор рассчитает массы, затем я соберу короткую таблицу. Литературный обзор и статья для этой задачи не нужны." },
  { kind: "tool_call", callId: "read-input", name: "Read", summary: "inputs/molecules.csv", input: { file_path: "inputs/molecules.csv" } },
  { kind: "tool_result", callId: "read-input", output: "name,smiles\nЭтанол,CCO\nУксусная кислота,CC(=O)O" },
  { kind: "message", text: "Исходные данные проверены. Передаю экспериментатору расчёт молекулярных масс доступными химическими инструментами." },
  { kind: "agent_start", invocationId: "experiment-1", role: "mle_solver", label: "Экспериментатор", task: "Рассчитать массы этанола и уксусной кислоты", background: true },
  { kind: "message", invocationId: "experiment-1", text: "Сначала получу свойства соединений по SMILES. Затем сверю значения расчётом и сохраню методику вместе с числами." },
  { kind: "tool_call", invocationId: "experiment-1", callId: "pubchem-1", name: "mcp__chem__pubchem_search_by_smiles", summary: "Свойства этанола · CCO", input: { smiles: "CCO" } },
  { kind: "tool_result", callId: "pubchem-1", output: '{"cid":702,"formula":"C2H6O","molecular_weight":46.07}' },
  { kind: "tool_call", invocationId: "experiment-1", callId: "pubchem-2", name: "mcp__chem__pubchem_search_by_smiles", summary: "Свойства уксусной кислоты · CC(=O)O", input: { smiles: "CC(=O)O" } },
  { kind: "tool_result", callId: "pubchem-2", output: '{"cid":176,"formula":"C2H4O2","molecular_weight":60.05}' },
  { kind: "message", text: "Пока экспериментатор считает, подготовлю структуру итогового документа: соединение, SMILES, молекулярная масса и краткое пояснение метода. Числа добавлю после его ответа." },
  { kind: "tool_call", callId: "manager-draft", name: "Write", summary: "Подготовка таблицы · draft.md", input: { file_path: "draft.md", content: "# Сравнение соединений\n\nРасчёт выполняется. Итоговые значения ещё не получены." } },
  { kind: "tool_result", callId: "manager-draft", output: "Файл draft.md сохранён." },
  { kind: "message", invocationId: "experiment-1", text: "Оба соединения найдены. Для трёх знаков после запятой выполню расчёт средней молекулярной массы; справочные значения с двумя знаками использую для сверки." },
  { kind: "tool_call", invocationId: "experiment-1", callId: "rdkit-first", name: "Bash", summary: "Проверка доступности RDKit", input: { command: "python3 -c 'import rdkit'" } },
  { kind: "tool_result", callId: "rdkit-first", output: "ModuleNotFoundError: No module named 'rdkit'\nВыбранное окружение не содержит пакет RDKit.", isError: true },
  { kind: "message", invocationId: "experiment-1", text: "В этом окружении RDKit недоступен. Перейду к вычислительному инструменту лаборатории, где подготовлены зависимости." },
  { kind: "tool_call", invocationId: "experiment-1", callId: "rdkit-second", name: "mcp__mle_kit__bash", summary: "Расчёт молекулярных масс", input: { command: "python3 mle/calculate_masses.py" } },
  { kind: "message", text: "Структура таблицы готова. Ожидаю проверенные значения от экспериментатора; исходные данные менять не требуется." },
  { kind: "tool_result", callId: "rdkit-second", output: '{\n  "ethanol": {"smiles": "CCO", "molecular_weight": 46.069},\n  "acetic_acid": {"smiles": "CC(=O)O", "molecular_weight": 60.052},\n  "difference": 13.983\n}' },
  { kind: "message", invocationId: "experiment-1", text: "Расчёт завершён: этанол — **46.069 г/моль**, уксусная кислота — **60.052 г/моль**. После округления до двух знаков значения согласуются со справочными.\n\nСохраню полные значения и описание способа расчёта, чтобы при доработке таблицы не выполнять эксперимент заново." },
  { kind: "tool_call", invocationId: "experiment-1", callId: "save-calculation", name: "Write", summary: "mle/molecular_mass.md", input: { file_path: "mle/molecular_mass.md", content: "# Расчёт молекулярных масс\n\nЭтанол: 46.069 г/моль. Уксусная кислота: 60.052 г/моль." } },
  { kind: "tool_result", callId: "save-calculation", output: "Файл mle/molecular_mass.md сохранён." },
  { kind: "message", invocationId: "experiment-1", text: "**Результат расчёта**\n\n| Соединение | Масса, г/моль |\n| --- | --- |\n| Этанол | 46.069 |\n| Уксусная кислота | 60.052 |\n\nРазница — **13.983 г/моль**. Методика и полные значения сохранены в `mle/molecular_mass.md`." },
  { kind: "agent_end", invocationId: "experiment-1", status: "complete" },
  { kind: "message", text: "Получил расчёт. Перед сохранением результата поручаю тому же экспериментатору короткую проверку округления — это отдельное задание." },
  { kind: "agent_start", invocationId: "experiment-2", role: "mle_solver", label: "Экспериментатор", task: "Проверить округление готовых значений", background: true },
  { kind: "message", invocationId: "experiment-2", text: "Проверил готовые значения: 46.069 → 46.07; 60.052 → 60.05. Новых вычислений молекулярных свойств не требуется." },
  { kind: "agent_end", invocationId: "experiment-2", status: "complete" },
  { kind: "tool_call", callId: "save-result", name: "Write", summary: "results/0001/result.md", input: { file_path: "results/0001/result.md", content: "# Сравнение\n\nЭтанол — 46.069 г/моль, уксусная кислота — 60.052 г/моль." } },
  { kind: "tool_result", callId: "save-result", output: "Файл results/0001/result.md сохранён." },
  { kind: "message", text: "Готово. В итоговом документе сохранены значения с тремя знаками после запятой и описание метода. Уксусная кислота тяжелее этанола на **13.983 г/моль**." }
];

export const fixture = raw.map((event, index) => ({
  ...event, id: `event-${index + 1}`, at: `21:${String(36 + Math.floor(index / 5)).padStart(2, "0")}:${String(index % 5 * 11).padStart(2, "0")}`
})) as ActivityEvent[];

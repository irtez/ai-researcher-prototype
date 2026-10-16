import { useState } from "react";
import { LAUNCH_EXAMPLES } from "./launchExamples";
import type { LaunchExample } from "./launchExamples";
import { t } from "./i18n";

const STORAGE_KEY = "holosophus.ui.examplesCollapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function LaunchExamples({ disabled, onApply }: {
  disabled: boolean;
  onApply: (example: LaunchExample) => void;
}) {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
    } catch {
      // A storage restriction must not prevent changing the current view.
    }
  };

  return <section className="launch-examples" aria-label="Примеры заданий">
    <div className="launch-examples-heading">
      <h2>Примеры заданий</h2>
      <button type="button" className="example-toggle" onClick={toggle}
        aria-expanded={!collapsed} aria-controls="launch-example-cards">
        {collapsed ? "Показать примеры" : "Скрыть примеры"}
      </button>
    </div>
    {!collapsed && <div id="launch-example-cards">
      <p className="launch-examples-hint">Выберите пример и при необходимости измените задание. Запуск начнётся после нажатия «Старт».</p>
      <div className="launch-example-grid">
        {LAUNCH_EXAMPLES.map(example => <article className="launch-example-card" key={example.id}>
          <small>{t(`profile.${example.profile}`)}</small>
          <h3>{example.title}</h3>
          <p>{example.description}</p>
          <span className="example-files">{example.files.length ? `Вложения: ${example.files.map(file => file.name).join(", ")}` : "Без вложений"}</span>
          <button type="button" disabled={disabled} onClick={() => onApply(example)}>Использовать пример</button>
        </article>)}
      </div>
    </div>}
  </section>;
}

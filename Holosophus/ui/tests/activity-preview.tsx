import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Layers, Pause, Play, RotateCcw, SkipForward } from "lucide-react";
import { MarkdownPreview } from "../src/App";
import { LanguageProvider } from "../src/i18n";
import { ActivityFeed } from "../src/activity/ActivityFeed";
import { fixture } from "./activity-fixture";
import "../src/styles.css";
import "./activity-preview.css";

function Preview() {
  const [count, setCount] = useState(13);
  const [playing, setPlaying] = useState(false);
  const [replay, setReplay] = useState(0);
  const controls = useRef<HTMLDivElement>(null);
  const [controlsHeight, setControlsHeight] = useState(0);
  const events = useMemo(() => fixture.slice(0, count), [count]);
  useLayoutEffect(() => {
    if (!controls.current) return;
    const observer = new ResizeObserver(() => setControlsHeight(controls.current?.offsetHeight ?? 0));
    observer.observe(controls.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setCount(value => Math.min(value + 1, fixture.length)), 1600);
    return () => window.clearInterval(timer);
  }, [playing]);
  useEffect(() => { if (count === fixture.length) setPlaying(false); }, [count]);
  const reset = () => { setPlaying(false); setCount(0); setReplay(value => value + 1); window.scrollTo(0, 0); };
  return <>
    <header className="preview-topbar"><span className="preview-logo"><Layers size={23} /></span>
      <strong>AI Researcher</strong><span className="preview-tag">Пример новой ленты</span>
    </header>
    <div className="preview-controls" ref={controls}>
      <p>События подготовлены заранее. Модель и инструменты не запускаются.</p>
      <div className="preview-buttons">
        <button className="preview-primary" onClick={() => {
          if (count === fixture.length) { reset(); setPlaying(true); } else setPlaying(value => !value);
        }}>{playing ? <Pause size={15} /> : <Play size={15} />}{playing ? "Пауза" : "Воспроизвести"}</button>
        <button disabled={playing || count === fixture.length} onClick={() => setCount(value => value + 1)}><SkipForward size={15} />Следующее событие</button>
        <button onClick={reset}><RotateCcw size={15} />Сначала</button>
        <button onClick={() => { setPlaying(false); setCount(fixture.length); }}>Показать всё</button>
        <span className="preview-counter">{count} / {fixture.length}</span>
      </div>
    </div>
    <main className="preview-main">
      <div className="preview-intro"><span className="preview-eyebrow">Детали запуска</span>
        <h1>Сравнение этанола и уксусной кислоты</h1>
        <p>Менеджер и каждый вызванный агент работают на своих подвкладках. Новые вкладки появляются справа, в порядке запуска.</p>
      </div>
      <ActivityFeed key={replay} events={events} stickyOffset={controlsHeight} renderContent={text => <MarkdownPreview content={text} />} />
      <div className="preview-end">{count === fixture.length ? "Пример завершён" : "Следующие события появятся при воспроизведении"}</div>
    </main>
  </>;
}

createRoot(document.getElementById("root")!).render(<LanguageProvider><Preview /></LanguageProvider>);

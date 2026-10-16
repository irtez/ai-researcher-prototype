import { useEffect, useState, type RefObject } from "react";
import { ArrowUp, MessageCircle } from "lucide-react";
import { t } from "./i18n";

/** A locator for the current operator question, independent of the viewed iteration. */
export function PendingNotice({ questionId, stage, target }: {
  questionId: string; stage?: string | null; target: RefObject<HTMLElement | null>;
}) {
  const [offscreen, setOffscreen] = useState(false);
  useEffect(() => {
    const card = target.current;
    if (!card) return;
    const observer = new IntersectionObserver(([entry]) => setOffscreen(!entry.isIntersecting));
    observer.observe(card);
    return () => observer.disconnect();
  }, [questionId, target]);
  if (!offscreen) return null;
  const label = t(stage === "sources" ? "approval.pendingSources" : stage === "plan" ? "approval.pendingPlan" : "approval.pendingQuestion");
  return <div className="pending-notice" role="status">
    <MessageCircle size={18} aria-hidden="true" />
    <span>{label}</span>
    <button onClick={() => {
      const card = target.current;
      card?.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
      card?.focus({ preventScroll: true });
    }}><ArrowUp size={14} aria-hidden="true" />{t("approval.goToQuestion")}</button>
  </div>;
}

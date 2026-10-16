export type AnswerAction = "approve" | "revise" | "answer";

export function buildAnswerPayload(
  sessionId: string,
  qid: string,
  action: AnswerAction,
  text?: string
) {
  const normalizedText = text?.trim();
  return {
    session_id: sessionId,
    qid,
    action,
    ...(normalizedText ? { text: normalizedText } : {})
  };
}

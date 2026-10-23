import type { AskSelection } from "./chat-messages";

export type AskHandoff = Partial<AskSelection> & { prompt?: string; draftId?: string };

/** A distinct URL also refreshes drafts retained by the router's page cache. */
export function prepareAsk(pod: string, context: AskHandoff): string {
  const draftId = crypto.randomUUID();
  sessionStorage.setItem(`podos.ask.${pod}`, JSON.stringify({ ...context, draftId }));
  return `/${pod}/ask?draft=${draftId}`;
}

export function readAskHandoff(pod: string): AskHandoff {
  if (typeof window === "undefined") return {};
  try { return JSON.parse(sessionStorage.getItem(`podos.ask.${pod}`) ?? "{}"); }
  catch { return {}; }
}

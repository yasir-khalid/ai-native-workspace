import { createStore } from "zustand/vanilla";
import { api, explain } from "./api";
import { streamRun } from "./sse";
import { applyEvent, uid, type AskSelection, type UiMessage } from "./chat-messages";

export type ChatSession = {
  threadId: string | null;
  messages: UiMessage[];
  running: boolean;
  runId?: string;
  cancelRequested?: boolean;
};
type State = { sessions: Record<string, ChatSession>; aliases: Record<string, string>; runningThreads: string[] };

/** Workspace-owned streams. Page unmounts never cancel a server run or its local subscriber. */
export class ChatSessions {
  readonly store = createStore<State>(() => ({ sessions: {}, aliases: {}, runningThreads: [] }));
  private streams = new Map<string, AbortController>();
  private alive = true;
  constructor(readonly pod: string, private changed: (threadId: string | null) => void) {}

  resolve(key: string) { return this.store.getState().aliases[key] ?? key; }
  get(key: string) { return this.store.getState().sessions[this.resolve(key)]; }
  private update(key: string, fn: (session: ChatSession) => ChatSession) {
    const resolved = this.resolve(key);
    this.store.setState(state => {
      const current = state.sessions[resolved];
      if (!current) return state;
      const session = fn(current);
      const sessions = { ...state.sessions, [resolved]: session };
      const running = Object.values(sessions).filter(s => s.running && s.threadId).map(s => s.threadId!);
      const runningThreads = running.join() === state.runningThreads.join() ? state.runningThreads : running;
      return { sessions, runningThreads };
    });
  }
  ensure(key: string, threadId: string | null, messages: UiMessage[]) {
    const current = this.get(key);
    if (!current) this.store.setState(state => ({ sessions: { ...state.sessions, [key]: { threadId, messages, running: false } } }));
    else if (!current.running && messages.some(message => message.role === "assistant" && message.runId &&
      !current.messages.some(local => local.runId === message.runId && local.status === "complete"))) {
      // A stream may have disconnected while the server finished. Prefer the persisted answer
      // on return; an older history response must never erase an optimistic or streaming turn.
      this.update(key, session => ({ ...session, messages }));
    }
  }
  private patch(key: string, id: string, fn: (message: UiMessage) => UiMessage) {
    this.update(key, s => ({ ...s, messages: s.messages.map(m => m.id === id ? fn(m) : m) }));
  }
  attach(key: string, runId: string) {
    const session = this.get(key);
    if (!session || this.streams.has(runId) || session.messages.some(m => m.runId === runId && m.status === "complete")) return;
    const id = `run-${runId}`;
    this.update(key, s => ({ ...s, running: true, runId, messages: [
      ...s.messages.filter(m => m.runId !== runId && m.id !== id),
      { id, runId, role: "assistant", segments: [], status: "running", createdAt: new Date() },
    ] }));
    void this.follow(key, runId, id);
  }
  private async follow(key: string, runId: string, assistantId: string) {
    if (this.streams.has(runId)) return;
    const abort = new AbortController();
    this.streams.set(runId, abort);
    let sequence = 0;
    try {
      for (let attempt = 0; attempt < 4 && !abort.signal.aborted; attempt++) {
        try {
          await streamRun(runId, this.pod, event => {
            if (event.sequence && event.sequence <= sequence) return;
            sequence = event.sequence || sequence;
            this.patch(key, assistantId, m => applyEvent(m, event));
            if (["run.completed", "run.failed", "stream.terminated"].includes(event.type)) {
              this.update(key, s => ({ ...s, running: false }));
            }
          }, abort.signal, sequence);
          if (!this.get(key)?.running) break;
          throw new Error("Connection interrupted");
        } catch (error) {
          if (abort.signal.aborted) return;
          if (attempt === 3) throw error;
          await new Promise(resolve => setTimeout(resolve, 500 * 2 ** attempt));
        }
      }
    } catch {
      this.patch(key, assistantId, m => ({ ...m, status: "incomplete", error: "Connection interrupted. Your answer continues in the background; reopen this chat to reconnect." }));
      this.update(key, s => ({ ...s, running: false }));
    } finally {
      if (this.streams.get(runId) === abort) {
        this.streams.delete(runId);
        if (this.alive) this.changed(this.get(key)?.threadId ?? null);
      }
    }
  }
  async send(key: string, text: string, selection: AskSelection, created: (threadId: string) => void) {
    if (this.get(key)?.running) return;
    const placeholder = uid("pending");
    this.update(key, s => ({ ...s, running: true, runId: undefined, cancelRequested: false, messages: [...s.messages,
      { id: uid("u"), role: "user", segments: [{ kind: "text", text }], callId: selection.callId, status: "complete", createdAt: new Date() },
      { id: placeholder, role: "assistant", segments: [], status: "running", createdAt: new Date() },
    ] }));
    try {
      let threadId = this.get(key)!.threadId;
      const isNew = !threadId;
      if (!threadId) {
        const thread = await api<{ thread_id: string }>(`/v1/pods/${this.pod}/threads`, { body: { research_case_id: selection.caseId ?? undefined } });
        threadId = thread.thread_id;
        this.update(key, s => ({ ...s, threadId }));
        this.store.setState(state => ({ aliases: { ...state.aliases, [threadId!]: this.resolve(key) } }));
      }
      const run = await api<{ run_id: string }>(`/v1/threads/${threadId}/runs`, { pod: this.pod, body: {
        mode: selection.mode, agent_id: selection.mode === "direct" ? selection.agentId : undefined,
        research_case_id: selection.caseId ?? undefined, message: text, call_id: selection.callId ?? null,
        client_request_id: crypto.randomUUID(),
      } });
      const assistantId = `run-${run.run_id}`;
      this.update(key, s => ({ ...s, runId: run.run_id, messages: s.messages.map(m => m.id === placeholder ? { ...m, id: assistantId, runId: run.run_id } : m) }));
      this.changed(threadId);
      if (isNew) created(threadId);
      if (this.get(key)?.cancelRequested) await api(`/v1/runs/${run.run_id}/cancel`, { pod: this.pod, method: "POST" });
      if (this.alive) await this.follow(key, run.run_id, assistantId);
    } catch (error) {
      const reason = explain(error);
      this.patch(key, placeholder, m => ({ ...m, status: "incomplete", error: reason, segments: [
        { kind: "tool", id: uid("error"), name: "podos.error", args: {}, isError: true, result: { message: reason } },
      ] }));
      this.update(key, s => ({ ...s, running: false }));
      if (this.alive) this.changed(this.get(key)?.threadId ?? null);
    }
  }
  async cancel(key: string) {
    this.update(key, s => ({ ...s, cancelRequested: true }));
    const runId = this.get(key)?.runId;
    if (runId) await api(`/v1/runs/${runId}/cancel`, { pod: this.pod, method: "POST" });
  }
  dispose() { this.alive = false; for (const abort of this.streams.values()) abort.abort(); this.streams.clear(); }
  resume() { this.alive = true; }
}

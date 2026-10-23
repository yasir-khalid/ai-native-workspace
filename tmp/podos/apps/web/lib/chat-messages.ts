import type { ThreadMessageLike } from "@assistant-ui/react";
import type { RunEvent, StoredMessage } from "./types";

type Segment =
  | { kind: "text"; text: string }
  | { kind: "tool"; id: string; name: string; args: Record<string, unknown>; result?: unknown; isError?: boolean };

export type UiMessage = {
  id: string;
  role: "user" | "assistant";
  segments: Segment[];
  status: "running" | "complete" | "incomplete";
  error?: string;
  createdAt: Date;
  runId?: string;
  callId?: string | null;
};

export type AskSelection = { mode: "auto" | "direct"; agentId: string | null; caseId: string | null; callId?: string | null };

let seq = 0;
export const uid = (p: string) => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

export function convert(m: UiMessage): ThreadMessageLike {
  const content = m.segments.map((s) =>
    s.kind === "text"
      ? ({ type: "text", text: s.text } as const)
      : ({
          type: "tool-call",
          toolCallId: s.id,
          toolName: s.name,
          args: s.args as never,
          result: s.result,
          isError: s.isError,
        } as const),
  );
  return {
    id: m.id,
    role: m.role,
    content: content.length ? content : [{ type: "text", text: "" }],
    createdAt: m.createdAt,
    metadata: { custom: { callId: m.callId ?? null } },
    status:
      m.role === "user"
        ? undefined
        : m.status === "running"
          ? { type: "running" }
          : m.status === "complete"
            ? { type: "complete", reason: "stop" }
            : { type: "incomplete", reason: "error", error: m.error },
  };
}

export function hydrate(stored: StoredMessage[]): UiMessage[] {
  return stored.map((m) => {
    if (m.role === "user") {
      return { id: m.message_id, role: "user", status: "complete", createdAt: new Date(m.created_at), callId: m.content.call_id, segments: [{ kind: "text", text: m.content.text }] };
    }
    const segs: Segment[] = [];
    if (m.content.agent_id) {
      segs.push({
        kind: "tool", id: `${m.message_id}-route`, name: "podos.route", args: {},
        result: { agent_id: m.content.agent_id, name: m.content.agent_name, version: m.content.agent_version, persisted: true },
      });
    }
    segs.push({ kind: "text", text: m.content.text });
    for (const a of m.content.artifacts ?? []) {
      segs.push({ kind: "tool", id: `${m.message_id}-${a.artifact_id}`, name: "podos.artifact", args: {}, result: a });
    }
    if (m.content.citations?.length) {
      segs.push({ kind: "tool", id: `${m.message_id}-src`, name: "podos.sources", args: {}, result: m.content.citations });
    }
    return { id: m.message_id, runId: m.run_id ?? undefined, role: "assistant", status: "complete", createdAt: new Date(m.created_at), callId: m.content.call_id, segments: segs };
  });
}

export function applyEvent(msg: UiMessage, e: RunEvent): UiMessage {
  const segs = [...msg.segments];
  const last = segs[segs.length - 1];
  switch (e.type) {
    case "agent.selected":
      segs.push({ kind: "tool", id: `route-${e.sequence}`, name: "podos.route", args: {}, result: e });
      break;
    case "message.delta":
      if (last?.kind === "text") segs[segs.length - 1] = { ...last, text: last.text + String(e.text ?? "") };
      else segs.push({ kind: "text", text: String(e.text ?? "") });
      break;
    case "tool.started":
      segs.push({ kind: "tool", id: `tool-${e.sequence}`, name: String(e.tool), args: { label: e.label } });
      break;
    case "tool.completed": {
      let matched = false;
      for (let i = segs.length - 1; i >= 0; i--) {
        const s = segs[i];
        if (s.kind === "tool" && s.name === e.tool && s.result === undefined) {
          segs[i] = { ...s, result: e, isError: e.status === "denied" };
          matched = true;
          break;
        }
      }
      // Some tools (e.g. an external agent's "agent.verification") complete without a
      // matching "started" event; surface them as a finished part rather than dropping them.
      if (!matched) segs.push({ kind: "tool", id: `tool-${e.sequence}`, name: String(e.tool), args: {}, result: e, isError: e.status === "denied" });
      break;
    }
    case "artifact.created":
      segs.push({ kind: "tool", id: `art-${e.sequence}`, name: "podos.artifact", args: {}, result: e.artifact });
      break;
    case "sources.available": {
      const sourcePart = { kind: "tool" as const, id: `src-${msg.id}`, name: "podos.registry", args: {}, result: e.items ?? [] };
      const index = segs.findIndex((s) => s.kind === "tool" && s.name === "podos.registry");
      if (index < 0) segs.push(sourcePart); else segs[index] = sourcePart;
      break;
    }
    case "run.completed": {
      const cites = (e.citations as unknown[]) ?? [];
      for (let i = segs.length - 1; i >= 0; i--) if (segs[i].kind === "tool" && ["podos.sources", "podos.registry"].includes((segs[i] as { name: string }).name)) segs.splice(i, 1);
      if (cites.length) segs.push({ kind: "tool", id: `src-${e.sequence}`, name: "podos.sources", args: {}, result: cites });
      return { ...msg, segments: segs, status: "complete" };
    }
    case "run.failed":
    case "stream.terminated":
      segs.push({ kind: "tool", id: `err-${e.sequence}`, name: "podos.error", args: {}, result: e, isError: true });
      return { ...msg, segments: segs, status: "incomplete", error: String(e.message ?? e.code ?? "failed") };
  }
  return { ...msg, segments: segs };
}

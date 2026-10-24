import { test } from "node:test";
import assert from "node:assert/strict";
import { ChatSessions } from "../lib/chat-sessions";
import { applyEvent, convert, hydrate } from "../lib/chat-messages";
import type { RunEvent } from "../lib/types";

const selection = { mode: "auto" as const, agentId: null, caseId: null, callId: "call-a" };
const event = (type: string, sequence: number, extra = {}) => ({ type, sequence, run_id: "r1", timestamp: "", ...extra }) as RunEvent;
async function until(check: () => boolean) {
  for (let i = 0; i < 100; i++) { if (check()) return; await new Promise(r => setTimeout(r, 10)); }
  assert.fail("Timed out waiting for chat state");
}
function transport() {
  const original = globalThis.fetch;
  const streams = new Map<string, ReadableStreamDefaultController<Uint8Array>>();
  const requests: { path: string; body?: Record<string, unknown>; headers?: HeadersInit }[] = [];
  let threads = 0, runs = 0;
  globalThis.fetch = async (input, init) => {
    const path = new URL(String(input)).pathname;
    requests.push({ path, body: init?.body ? JSON.parse(String(init.body)) : undefined, headers: init?.headers });
    if (path.endsWith("/events")) return new Response(new ReadableStream({
      start(controller) {
        streams.set(path.split("/")[3], controller);
        init?.signal?.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")), { once: true });
      },
    }));
    if (path.endsWith("/threads")) return Response.json({ thread_id: `t${++threads}` });
    if (path.endsWith("/runs")) return Response.json({ run_id: `r${++runs}` });
    if (path.endsWith("/cancel")) {
      const id = path.split("/")[3];
      queueMicrotask(() => send(id, event("run.failed", 99, { code: "cancelled" })));
      return Response.json({ state: "CANCELLED" });
    }
    throw new Error(`Unexpected request ${path}`);
  };
  function send(id: string, e: RunEvent) {
    streams.get(id)?.enqueue(new TextEncoder().encode(`event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`));
  }
  return { streams, requests, send, restore() { globalThis.fetch = original; } };
}

test("workspace streams keep two chats running while views leave and return", async () => {
  const net = transport();
  const sessions = new ChatSessions("pod_alpha", () => {});
  try {
    sessions.ensure("draft-a", null, []);
    const a = sessions.send("draft-a", "Question A", selection, () => {});
    await until(() => net.streams.has("r1"));
    // The page has left; a second chat subscribes to its own state in the same workspace.
    sessions.ensure("draft-b", null, []);
    const b = sessions.send("draft-b", "Question B", { ...selection, callId: null }, () => {});
    await until(() => net.streams.has("r2"));
    assert.deepEqual(sessions.store.getState().runningThreads, ["t1", "t2"]);
    net.send("r1", event("message.delta", 1, { text: "Answer A" }));
    net.send("r2", event("message.delta", 1, { text: "Answer B" }));
    net.send("r1", event("run.completed", 2));
    await a;
    assert.equal(sessions.get("t1")?.messages.length, 2);
    assert.equal(sessions.get("t1")?.messages[1].segments[0].kind, "text");
    assert.deepEqual(sessions.store.getState().runningThreads, ["t2"]);
    sessions.ensure("t1", "t1", []);
    sessions.attach("t1", "r1"); // stale active_run response must not duplicate a finished assistant
    assert.equal(net.requests.filter(r => r.path === "/v1/runs/r1/events").length, 1);
    net.send("r2", event("run.completed", 2));
    await b;
    assert.deepEqual(sessions.store.getState().runningThreads, []);
    assert.equal(net.requests.filter(r => r.path.endsWith("/cancel")).length, 0);
    assert.deepEqual(net.requests.filter(r => r.path.endsWith("/runs")).map(r => r.body?.call_id), ["call-a", null]);
  } finally { sessions.dispose(); net.restore(); }
});

test("page reload replays an active run without a second user message; stop is explicit", async () => {
  const net = transport();
  const sessions = new ChatSessions("pod_alpha", () => {});
  try {
    const initial = hydrate([{ message_id: "u1", role: "user", run_id: "r1", created_at: new Date().toISOString(), content: { text: "Question", call_id: "call-a" } }]);
    sessions.ensure("t1", "t1", initial);
    sessions.attach("t1", "r1");
    await until(() => net.streams.has("r1"));
    net.send("r1", event("message.delta", 1, { text: "First" }));
    net.send("r1", event("message.delta", 1, { text: "DUPLICATE" }));
    await until(() => sessions.get("t1")!.messages[1].segments.length > 0);
    assert.deepEqual(sessions.get("t1")!.messages[1].segments, [{ kind: "text", text: "First" }]);
    await sessions.cancel("t1");
    await until(() => !sessions.get("t1")!.running);
    assert.equal(sessions.get("t1")!.messages.length, 2);
    assert.equal(net.requests.filter(r => r.path.endsWith("/runs")).length, 0);
    assert.equal(net.requests.filter(r => r.path.endsWith("/cancel")).length, 1);
  } finally { sessions.dispose(); net.restore(); }
});

test("streaming source registry is replaced by final cited-only snapshot", () => {
  const message = { id: "a1", role: "assistant" as const, segments: [], status: "running" as const, createdAt: new Date() };
  const registry = [{ ref: 1, text: "A" }, { ref: 2, text: "B" }];
  let result = applyEvent(message, event("sources.available", 1, { items: registry }));
  result = applyEvent(result, event("message.delta", 2, { text: "Claim [2]" }));
  result = applyEvent(result, event("run.completed", 3, { citations: [registry[1]] }));
  const sources = result.segments.filter(s => s.kind === "tool");
  assert.equal(result.status, "complete");
  assert.deepEqual(sources.map(s => s.kind === "tool" && [s.name, s.result]), [["podos.sources", [registry[1]]]]);
});

test("effect cleanup and resume never let an old subscriber remove a new one", async () => {
  const net = transport();
  const sessions = new ChatSessions("pod_alpha", () => {});
  try {
    sessions.ensure("t1", "t1", []);
    sessions.attach("t1", "r1");
    await until(() => net.streams.has("r1"));
    sessions.dispose();
    sessions.resume();
    sessions.attach("t1", "r1");
    await new Promise(resolve => setTimeout(resolve, 10));
    sessions.attach("t1", "r1");
    assert.equal(net.requests.filter(r => r.path.endsWith("/events")).length, 2);
    net.send("r1", event("message.delta", 1, { text: "Recovered" }));
    net.send("r1", event("run.completed", 2));
    await until(() => !sessions.get("t1")!.running);
    assert.equal(sessions.get("t1")!.messages.length, 1);
  } finally { sessions.dispose(); net.restore(); }
});

test("returning after a connection loss adopts the persisted completed answer", () => {
  const sessions = new ChatSessions("pod_alpha", () => {});
  sessions.ensure("t1", "t1", [{ id: "run-r1", runId: "r1", role: "assistant", segments: [{ kind: "text", text: "Partial" }], status: "incomplete", createdAt: new Date() }]);
  const stored = hydrate([{ message_id: "a1", run_id: "r1", role: "assistant", created_at: new Date().toISOString(), content: { text: "Complete server answer" } }]);
  sessions.ensure("t1", "t1", stored);
  assert.equal(sessions.get("t1")!.messages[0].status, "complete");
  assert.deepEqual(sessions.get("t1")!.messages[0].segments, [{ kind: "text", text: "Complete server answer" }]);
  sessions.ensure("t1", "t1", []); // late stale response cannot erase it
  assert.equal(sessions.get("t1")!.messages.length, 1);
  sessions.dispose();
});


test("saved result cards and completed activity survive hydration without losing answer text", () => {
  const result = { kind: "court_availability", version: 1, id: "courts-1", source: "Sportscanner", truncated: false,
    venues: [{ name: "Example Sports Centre", operator: "Operator", address: "London", date: "Sun, Oct 25",
      distance_miles: 1.2, stale: true, last_refreshed: "2026-10-24T12:00:00Z",
      slots: [{ start: "17:00", end: "18:00", price_gbp: 12.8, booking_url: "https://booking.example.com/1" }] }] };
  const events = [event("run.started", 1), event("agent.selected", 2, { agent_id: "sportscanner", name: "Sportscanner" }),
    event("tool.started", 3, { tool: "agent.progress", label: "Checking availability" }),
    event("tool.completed", 4, { tool: "agent.progress", status: "ok", summary: "Checked availability" }),
    event("ui.result", 5, { result }), event("run.completed", 6)];
  events[0].timestamp = "2026-10-24T12:00:00Z";
  events.at(-1)!.timestamp = "2026-10-24T12:00:08Z";
  const [saved] = hydrate([{ message_id: "m1", role: "assistant", run_id: "r1", created_at: events.at(-1)!.timestamp,
    content: { text: "Answer with important caveats", agent_id: "sportscanner", activity: events } }]);
  assert.equal(saved.durationMs, 8000);
  assert.deepEqual(saved.segments.filter(s => s.kind === "tool").map(s => s.kind === "tool" && s.name),
    ["podos.route", "agent.progress", "podos.result"]);
  const rendered = convert(saved);
  assert.equal(rendered.metadata?.custom?.structuredResults, true);
  assert.equal(rendered.metadata?.custom?.activityDurationMs, 8000);
  assert.ok(Array.isArray(rendered.content));
  assert.equal(rendered.content.at(-1)?.type, "text");
  assert.equal(rendered.content.filter(s => s.type === "text").map(s => s.type === "text" && s.text).join(""), "Answer with important caveats");
  // Duplicate protocol snapshots must not duplicate the booking card.
  assert.equal(applyEvent(saved, event("ui.result", 7, { result })).segments.filter(s => s.kind === "tool" && s.name === "podos.result").length, 1);
});

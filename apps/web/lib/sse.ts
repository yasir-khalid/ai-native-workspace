import { API_URL } from "./api";
import type { RunEvent } from "./types";

/**
 * Fetch-based SSE reader (EventSource can't send the X-Active-Pod header).
 * Supports resume via Last-Event-ID; the stream is bound to the pod it was opened in.
 */
export async function streamRun(
  runId: string,
  pod: string,
  onEvent: (e: RunEvent) => void,
  signal: AbortSignal,
  lastEventId?: number,
): Promise<void> {
  const headers: Record<string, string> = { "X-Active-Pod": pod, Accept: "text/event-stream" };
  if (lastEventId) headers["Last-Event-ID"] = String(lastEventId);
  const res = await fetch(`${API_URL}/v1/runs/${runId}/events`, { credentials: "include", headers, signal });
  if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let type = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) type = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!data) continue;
      const evt = { type, ...JSON.parse(data) } as RunEvent;
      onEvent(evt);
      if (type === "run.completed" || type === "run.failed" || type === "stream.terminated") return;
    }
  }
}

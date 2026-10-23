/**
 * Workspace API client. The browser never enforces permissions: every call is
 * re-authorised server-side. The active pod is a *selection* sent per request
 * (path or X-Active-Pod) and validated against live membership by the API.
 */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8400";

export class ApiError extends Error {
  status: number;
  code: string;
  reason?: string;
  constructor(status: number, code: string, detail: string, reason?: string) {
    super(detail || code);
    this.status = status;
    this.code = code;
    this.reason = reason;
  }
}

type Opts = { pod?: string; method?: string; body?: unknown; signal?: AbortSignal };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.pod) headers["X-Active-Pod"] = opts.pod;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${API_URL}${path}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    credentials: "include",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
    cache: "no-store",
  });
  if (!res.ok) {
    let data: { code?: string; detail?: string; reason?: string } = {};
    try {
      data = await res.json();
    } catch {}
    throw new ApiError(res.status, data.code ?? "error", data.detail ?? res.statusText, data.reason);
  }
  return (await res.json()) as T;
}

export const fetcher = ([path, pod]: [string, string | undefined]) => api(path, { pod });

/** Human wording for policy denials surfaced to users. */
export function explain(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong.";
  const r = err.reason ?? err.code;
  const map: Record<string, string> = {
    capability_not_entitled: "Your entitlements in this pod don't include this capability.",
    capability_emergency_disabled: "This capability is temporarily disabled by the platform team.",
    agent_emergency_disabled: "This agent is temporarily disabled by the platform team.",
    agent_not_entitled: "You're not entitled to this agent.",
    release_direct_invoke_disabled: "This agent isn't released for direct use in this pod yet.",
    classification_exceeds_clearance: "This content exceeds your clearance.",
    not_a_pod_member: "You're no longer a member of this pod.",
    stale_version: "This item changed since you opened it. Refresh and try again.",
    not_assigned_approver: "Only the assigned approver can decide this item.",
    expired: "The approval window has expired.",
    pod_rate_limit: "Your pod has hit its run rate limit. Try again in a minute.",
    pod_concurrency_limit: "Your pod has too many runs in progress.",
  };
  return map[r] ?? err.message;
}

export function relTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  if (d < 86400 * 30) return `${Math.floor(d / 86400)}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function shortDate(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" });
}

export async function downloadArtifact(pod: string, artifactId: string): Promise<void> {
  const { url } = await api<{ url: string }>(`/v1/artifacts/${artifactId}/download`, { pod, method: "POST" });
  // The link is short-lived and bound to this signed-in user; the API re-checks membership on redemption.
  window.location.href = url;
}

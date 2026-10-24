import type { CatalogueAgent } from "./types";

/** Catalogue membership is an access decision; actions are a live availability decision. */
export function canChatWithAgent(agent: CatalogueAgent): boolean {
  return agent.kind !== "background" && agent.actions.direct;
}

export function agentHealth(agent: CatalogueAgent) {
  // The fallback also supports a browser refreshing during an API deployment.
  if (agent.health_status === "not_connected" || agent.unavailable_reason === "agent_deployment_missing") {
    return { label: "Not connected", state: "not_connected" } as const;
  }
  return agent.healthy
    ? { label: "Healthy", state: "healthy" } as const
    : { label: "Unhealthy", state: "unhealthy" } as const;
}

export function agentAvailabilityNote(agent: CatalogueAgent): string | null {
  if (agent.kind === "background" && agent.actions.background) return "Runs in the background. Manage it in Inbox → Monitors.";
  if (canChatWithAgent(agent)) return null;
  if (agent.unavailable_reason === "agent_emergency_disabled") return "Temporarily disabled by the platform team.";
  if (agent.release_stage === "PAUSED") return "Paused. Your access is retained.";
  if (agent.health_status === "not_connected" || agent.unavailable_reason === "agent_deployment_missing") return "No deployment connected. Your access is retained.";
  if (!agent.healthy) return "Service health check failed. Availability updates automatically.";
  if (agent.actions.auto) return "Available through Auto; direct chat is disabled.";
  if (agent.unavailable_reason === "agent_not_published") return "Awaiting publication. Your access is retained.";
  return "Unavailable in the current release. Your access is retained.";
}

export function orderedAgents(agents: CatalogueAgent[]): CatalogueAgent[] {
  // Keep positions stable across health changes, with Auto first and background last.
  const rank = { router: 0, specialist: 1, background: 2 };
  return [...agents].sort((a, b) => rank[a.kind] - rank[b.kind] || a.name.localeCompare(b.name));
}

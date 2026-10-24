import { CircleCheckIcon, CircleAlertIcon, UnplugIcon } from "lucide-react";
import { AgentTile, agentDescription } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { agentAvailabilityNote, agentHealth, canChatWithAgent } from "@/lib/agent-availability";
import type { CatalogueAgent } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Shared status treatment in the chat picker and command search. */
export function AgentMenuDetails({ agent }: { agent: CatalogueAgent }) {
  const health = agentHealth(agent);
  const disabled = !canChatWithAgent(agent);
  const Icon = health.state === "healthy" ? CircleCheckIcon : health.state === "not_connected" ? UnplugIcon : CircleAlertIcon;
  const note = agentAvailabilityNote(agent);
  return (
    <>
      <span className={cn("shrink-0", disabled && "grayscale opacity-60")}><AgentTile agentId={agent.agent_id} size="sm" /></span>
      <span className="grid min-w-0 flex-1 gap-1">
        <span className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span className={cn("text-sm font-medium", disabled && "text-muted-foreground")}>{agent.agent_id === "auto-router" ? "Auto" : agent.name}</span>
          <Badge variant={health.state === "healthy" ? "green" : health.state === "unhealthy" ? "amber" : "gray"} className="h-5 px-1.5 text-[10px]">
            <Icon data-icon="inline-start" aria-hidden="true" />{health.label}
          </Badge>
        </span>
        <span className="line-clamp-2 text-xs text-muted-foreground">
          {agent.agent_id === "auto-router" ? "Picks the best available agent for each request" : agentDescription(agent.agent_id, agent.description)}
        </span>
        {(agent.kind === "background" || agent.release_stage?.startsWith("POD_PILOT")) && (
          <span className="flex gap-1">
            {agent.kind === "background" && <Badge variant="gray" className="text-[10px]">Background</Badge>}
            {agent.release_stage?.startsWith("POD_PILOT") && <Badge variant="outline" className="text-[10px]">Preview</Badge>}
          </span>
        )}
        {note && <span className="text-xs leading-relaxed text-muted-foreground">{note}</span>}
      </span>
    </>
  );
}

"use client";

import { useEffect, useRef, useState, type FC, type PropsWithChildren } from "react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import {
  ArrowDownToLineIcon, BadgeCheckIcon, CheckIcon, ChevronDownIcon, CircleAlertIcon, FileTextIcon,
  LoaderCircleIcon, PresentationIcon, SearchIcon, ShieldXIcon, TriangleAlertIcon,
} from "lucide-react";
import { toast } from "sonner";
import { AgentTile, agentName, Tile } from "@/components/common";
import { Button } from "@/components/ui/button";
import { CollapsibleTrigger } from "@/components/assistant-ui/elements/collapsible-root";
import { ToolGroupContent, ToolGroupRoot } from "@/components/assistant-ui/elements/tool-group.aui";
import type { ThreadGroupPart } from "@/components/assistant-ui/elements/thread.aui";
import { usePod } from "@/components/shell/session";
import { useEvidence } from "./evidence";
import { downloadArtifact, explain, shortDate } from "@/lib/api";
import type { ArtifactRef, Citation } from "@/lib/types";
import { cn } from "@/lib/utils";
import { formatClock } from "@/lib/calls";

const DENIAL: Record<string, string> = {
  scope_violation: "Blocked by the platform: that request reached outside this pod",
  capability_not_entitled: "Not available: your role in this pod doesn't include this export",
  capability_emergency_disabled: "Temporarily disabled by the platform team",
  tool_budget_exceeded: "Stopped: this answer hit its step limit",
  not_in_delegation: "Not available in this chat",
};

function Route({ result }: { result: Record<string, unknown> }) {
  if (result.persisted) return null;
  return (
    <div className="mb-2 flex items-center gap-2 text-[13px] text-muted-foreground">
      <AgentTile agentId={String(result.agent_id)} size="sm" />
      <span className="font-medium text-foreground">{String(result.name ?? agentName(String(result.agent_id)))}</span>
      {result.mode === "auto" ? <span className="text-subtle">· chosen by Auto</span> : null}
    </div>
  );
}

function Activity({ name, args, result, isError }: { name: string; args: Record<string, unknown>; result?: Record<string, unknown>; isError?: boolean }) {
  const pending = result === undefined;
  const code = String(result?.code ?? "");
  const search = name === "research.search";
  const progress = name === "agent.progress";
  // An external agent can report how it checked its own answer; surface it as a status, not a failure.
  const verification = name === "agent.verification";
  const warning = verification && String(result?.status ?? "") !== "ok";
  let text: string;
  if (pending) text = String(args.label ?? "Working");
  else if (isError) text = DENIAL[code] ?? "Not allowed";
  else if (progress || verification) text = String(result?.summary ?? args.label ?? "Working");
  else if (search) text = `${String(args.label ?? "Searched the vault").replace(/^Searching/, "Searched")} · ${String(result?.summary ?? "")}`;
  else text = `Created ${String(result?.summary ?? "file")}`;
  const Icon = pending
    ? LoaderCircleIcon
    : isError
      ? code === "scope_violation" ? ShieldXIcon : CircleAlertIcon
      : verification
        ? warning ? TriangleAlertIcon : BadgeCheckIcon
        : search ? SearchIcon : CheckIcon;
  const tone = pending
    ? "animate-spin text-blue-700 motion-reduce:animate-none"
    : isError ? "text-red-700" : warning ? "text-amber-900" : verification ? "text-green-800" : search ? "text-blue-700" : "text-green-800";
  return (
    <div
      role="status"
      className={cn("flex items-center gap-2 py-1 text-[13px]", isError ? "text-red-900" : warning ? "text-amber-900" : "text-muted-foreground")}
    >
      <Icon className={cn("size-3.5 shrink-0", tone)} />
      <span className={cn("truncate", pending && "shimmer")}>{text}</span>
    </div>
  );
}

const KB = (n: number) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

export function FileCard({ artifact, className }: { artifact: ArtifactRef; className?: string }) {
  const { pod } = usePod();
  const [busy, setBusy] = useState(false);
  const pptx = artifact.filename.endsWith(".pptx");
  return (
    <div className={cn("my-3 flex w-full max-w-sm items-center gap-3 rounded-lg border bg-background p-3 ", className)}>
      <Tile tone={pptx ? "amber" : "blue"}>{pptx ? <PresentationIcon /> : <FileTextIcon />}</Tile>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{artifact.title || artifact.filename}</div>
        <div className="truncate text-xs text-muted-foreground">
          {pptx ? "PowerPoint" : "Word"} · {KB(artifact.size_bytes)} · Private to this pod
        </div>
      </div>
      <Button
        variant="ghost"
        size="icon"
        onClick={async () => {
          setBusy(true);
          try {
            await downloadArtifact(pod, artifact.artifact_id);
          } catch (e) {
            toast.error(explain(e));
          } finally {
            setBusy(false);
          }
        }}
        disabled={busy}
        className="size-8 rounded-md text-muted-foreground hover:bg-gray-100 hover:text-foreground"
        aria-label={`Download ${artifact.filename}`}
      >
        <ArrowDownToLineIcon className="size-4" />
      </Button>
    </div>
  );
}

function Sources({ items }: { items: Citation[] }) {
  const open = useEvidence();
  const [expanded, setExpanded] = useState(false);
  if (!items.length) return null;
  return <div className="mt-4">
    <div className="mb-2 text-xs font-medium text-muted-foreground">Sources · {items.length} cited {items.every(c => c.call_id) ? "passages" : "references"}</div>
    <div className="flex flex-wrap gap-2">
      {(expanded ? items : items.slice(0, 4)).map(c => <Button key={`${c.ref}-${c.doc_id || c.url}`} variant="ghost" onClick={() => open(c, items)}
        className="h-auto max-w-64 justify-start gap-2 rounded-md border bg-background px-2.5 py-1.5 text-left hover:border-blue-400 hover:bg-blue-50">
        <span className="grid size-5 shrink-0 place-items-center rounded-full bg-blue-100 text-[11px] font-medium text-blue-900">{c.ref}</span>
        <span className="min-w-0"><span className="block truncate text-xs font-medium">{c.call_id ? c.speaker || "Transcript passage" : c.title}</span>
          <span className="block truncate text-[11px] text-muted-foreground">{c.speaker ? `${c.ticker || "Transcript"}${c.start !== undefined ? ` · ${formatClock(c.start)}${c.end !== undefined ? ` – ${formatClock(c.end)}` : ""}` : ""}` : `${c.source} · ${shortDate(c.published_at)}`}</span>
        </span>
      </Button>)}
    </div>
    {items.length > 4 && <Button variant="ghost" size="sm" className="mt-2 text-xs text-muted-foreground" onClick={() => setExpanded(value => !value)}>{expanded ? "Show fewer sources" : `Show all ${items.length} sources`}</Button>}
  </div>;
}

function ErrorPart({ result }: { result: Record<string, unknown> }) {
  return (
    <div className="my-2 flex items-start gap-2 rounded-md border border-red-300 bg-red-100 px-3 py-2.5 text-sm text-red-900">
      <CircleAlertIcon className="mt-0.5 size-4 shrink-0" />
      <span>{String(result.message ?? "Something went wrong.")}</span>
    </div>
  );
}

export const PodosToolPart: ToolCallMessagePartComponent = ({ toolName, args, result, isError }) => {
  const r = (result ?? undefined) as Record<string, unknown> | undefined;
  switch (toolName) {
    case "podos.route":
      return r ? <Route result={r} /> : null;
    case "podos.artifact":
      return r ? <FileCard artifact={r as unknown as ArtifactRef} /> : null;
    case "podos.registry":
      return null;
    case "podos.sources":
      return <Sources items={(result as Citation[]) ?? []} />;
    case "podos.error":
      return r ? <ErrorPart result={r} /> : null;
    default:
      return <Activity name={toolName} args={(args ?? {}) as Record<string, unknown>} result={r} isError={isError} />;
  }
};

/**
 * The run timeline (route pick + progress/verification steps) grouped into one collapsible block.
 * While the answer is still streaming it stays open so the steps are visible; once the turn settles it
 * collapses to a single summary line, and a click re-opens it (and then it stays put, since the user
 * took over).
 */
export function ActivityGroup({ group, children }: PropsWithChildren<{ group: ThreadGroupPart }>) {
  const running = group.status?.type === "running";
  const steps = group.indices.length;
  const [open, setOpen] = useState(running);
  const userToggled = useRef(false);
  useEffect(() => {
    if (!userToggled.current) setOpen(running);
  }, [running]);
  const seconds =
    group.timing?.completedAt !== undefined
      ? Math.max(1, Math.round((group.timing.completedAt - group.timing.startedAt) / 1000))
      : undefined;
  const label = running ? "Thinking…" : seconds !== undefined ? `Thought for ${seconds}s` : "Activity";
  return (
    <ToolGroupRoot
      variant="ghost"
      open={open}
      onOpenChange={(next) => {
        userToggled.current = true;
        setOpen(next);
      }}
    >
      <CollapsibleTrigger className="flex items-center gap-1.5 py-1 text-[13px] text-muted-foreground transition-colors hover:text-foreground">
        <ChevronDownIcon className={cn("size-3.5 shrink-0 transition-transform duration-200 motion-reduce:transition-none", !open && "-rotate-90")} />
        <span>{label}</span>
        {!running && steps > 0 && (
          <span className="text-subtle">
            · {steps} step{steps === 1 ? "" : "s"}
          </span>
        )}
      </CollapsibleTrigger>
      <ToolGroupContent>{children}</ToolGroupContent>
    </ToolGroupRoot>
  );
}

export const PlainGroup: FC<PropsWithChildren> = ({ children }) => <div className="mb-1 flex flex-col">{children}</div>;

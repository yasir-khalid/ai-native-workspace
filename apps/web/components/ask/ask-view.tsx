"use client";

import { useEffect, useMemo, useState } from "react";
import { useAui } from "@assistant-ui/react";
import { useSWRConfig } from "swr";
import { CheckIcon, ChevronDownIcon, FolderIcon, LockIcon } from "lucide-react";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AgentTile, agentDescription } from "@/components/common";
import { Button } from "@/components/ui/button";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { useCallPlayer } from "@/components/calls/calls-player";
import type { CatalogueAgent, ResearchCase, StoredMessage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { readAskHandoff } from "@/lib/ask-handoff";
import { ActivityGroup, PodosToolPart } from "./parts";
import { CallComposer, CallComposerContext, UserCallReference } from "./call-composer";
import { EvidenceProvider } from "./evidence";
import { hydrate, PodosRuntimeProvider, type AskSelection } from "./podos-runtime";

function Welcome() {
  const { me } = usePod();
  return (
    <div className="mb-7 px-2 text-center">
      <h1 className="text-[32px] leading-tight font-bold tracking-[-0.025em] md:text-[40px]">What are we looking into, {me.display_name.split(" ")[0]}?</h1>
    </div>
  );
}

function Footnote() {
  return (
    <p className="flex items-center justify-center gap-1.5 text-center text-xs text-subtle">
      <LockIcon className="size-3" />
      Agents use sources available to you, including connected services. Check the sources; agents can make mistakes.
    </p>
  );
}

function AgentRow({ agentId, active, title, desc, badge, disabled, onClick }: { agentId: string; active: boolean; title: string; desc: string; badge?: string; disabled?: boolean; onClick: () => void }) {
  return (
    <DropdownMenuItem disabled={disabled} onClick={onClick} className="items-start gap-3 rounded-md px-2.5 py-2">
      <AgentTile agentId={agentId} size="sm" />
      <div className="grid min-w-0 flex-1 gap-0.5">
        <span className="flex items-center gap-2 text-sm">
          {title}
          {badge && <span className="rounded-full bg-amber-200 px-1.5 text-[10px] font-medium text-amber-900">{badge}</span>}
        </span>
        <span className="line-clamp-2 text-xs text-muted-foreground">{desc}</span>
      </div>
      <CheckIcon className={cn("mt-0.5 size-4", !active && "invisible")} />
    </DropdownMenuItem>
  );
}

function AgentSwitcher({ agents, sel, setSel }: { agents: CatalogueAgent[]; sel: AskSelection; setSel: (s: AskSelection) => void }) {
  // The server already filters this catalogue by entitlement. A temporary outage
  // disables invocation, but should not make a connected agent disappear.
  const direct = agents.filter((a) => a.kind === "specialist" && (a.actions.direct || a.unavailable_reason === "agent_unhealthy"));
  const autoAvailable = agents.some((a) => a.agent_id === "auto-router" && a.actions.direct);
  const current = sel.mode === "auto" ? "Auto" : direct.find((a) => a.agent_id === sel.agentId)?.name ?? "Choose agent";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" className="h-9 gap-2 px-2 text-[15px] font-semibold hover:bg-gray-100" aria-label="Choose agent">
            <AgentTile agentId={sel.mode === "auto" ? "auto-router" : sel.agentId ?? "auto-router"} size="sm" />
            {current}
            <ChevronDownIcon className="size-4 text-muted-foreground" />
          </Button>
        }
      />
      <DropdownMenuContent className="w-80 p-1.5" align="start">
        <DropdownMenuGroup>
          {autoAvailable && (
            <AgentRow
              agentId="auto-router"
              active={sel.mode === "auto"}
              title="Auto"
              desc="Picks the best agent available to you for each request"
              onClick={() => setSel({ ...sel, mode: "auto", agentId: null })}
            />
          )}
          {autoAvailable && direct.length > 0 && <DropdownMenuSeparator />}
          {direct.map((a) => (
            <AgentRow
              key={a.agent_id}
              agentId={a.agent_id}
              active={sel.mode === "direct" && sel.agentId === a.agent_id}
              title={a.name}
              desc={!a.healthy ? "The connected service is offline. Availability updates automatically." : agentDescription(a.agent_id, a.description)}
              badge={!a.healthy ? "Offline" : a.release_stage?.startsWith("POD_PILOT") ? "Preview" : undefined}
              disabled={!a.actions.direct}
              onClick={() => setSel({ ...sel, mode: "direct", agentId: a.agent_id })}
            />
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function CasePicker({ cases, sel, setSel, locked }: { cases: ResearchCase[]; sel: AskSelection; setSel: (s: AskSelection) => void; locked: boolean }) {
  const current = cases.find((c) => c.case_id === sel.caseId);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        disabled={locked}
        render={
          <Button variant="outline" className="h-8 max-w-64 justify-start gap-1.5 px-2.5 text-[13px] font-normal text-gray-900 hover:bg-gray-100 disabled:cursor-default disabled:opacity-100 disabled:hover:bg-background" aria-label="Research case">
            <FolderIcon className="size-3.5" />
            <span className="truncate">{current ? current.title : "No case"}</span>
            {!locked && <ChevronDownIcon className="size-3.5" />}
          </Button>
        }
      />
      <DropdownMenuContent className="w-80 p-1.5" align="end">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Ground this chat in a research case</DropdownMenuLabel>
          <DropdownMenuItem onClick={() => setSel({ ...sel, caseId: null })} className="rounded-lg">
            No case {!sel.caseId && <CheckIcon className="ml-auto size-4" />}
          </DropdownMenuItem>
          {cases.map((c) => (
            <DropdownMenuItem key={c.case_id} onClick={() => setSel({ ...sel, caseId: c.case_id })} className="rounded-lg">
              <span className="truncate">{c.title}</span>
              {sel.caseId === c.case_id && <CheckIcon className="ml-auto size-4" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AskView({ threadId, draftId, onCreated }: { threadId: string | null; draftId?: string | null; onCreated: (id: string) => void }) {
  const { pod } = usePod();
  const { current: activeCall } = useCallPlayer();
  const { mutate } = useSWRConfig();
  const agents = usePodData<CatalogueAgent[]>(`/v1/pods/${pod}/agents`, { refreshInterval: 15_000 });
  const cases = usePodData<ResearchCase[]>(`/v1/pods/${pod}/research-cases`);
  const thread = usePodData<{ messages: StoredMessage[]; case_id: string | null; active_run: { run_id: string } | null }>(
    threadId ? `/v1/threads/${threadId}` : null,
    { revalidateOnFocus: false },
  );
  // Hand-off from an agent, research case, Today or a call, read once.
  // The view can mount more than once while the route settles, so the key is cleared shortly after
  // mount rather than on first read.
  const [handoff] = useState(() => {
    const context = readAskHandoff(pod);
    return !threadId && draftId && context.draftId === draftId ? context : {};
  });
  useEffect(() => {
    if (handoff.prompt || handoff.agentId || handoff.caseId || handoff.callId) {
      const timer = setTimeout(() => {
        if (readAskHandoff(pod).draftId === handoff.draftId) sessionStorage.removeItem(`podos.ask.${pod}`);
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [pod, handoff]);
  const [callOverride, setCallOverride] = useState<string | null | undefined>(() => threadId ? undefined : handoff.callId ?? null);
  const lastUser = thread.data?.messages.findLast((message) => message.role === "user");
  const callId = callOverride !== undefined ? callOverride : lastUser?.content.call_id ?? null;
  const [prefill, setPrefill] = useState<string | null>(handoff.prompt ?? null);
  const [sel, setSel] = useState<AskSelection>(() => ({
    mode: handoff.agentId ? "direct" : "auto", agentId: handoff.agentId ?? null, caseId: handoff.caseId ?? null,
  }));

  const effectiveSel = useMemo<AskSelection>(() => {
    const list = agents.data ?? [];
    const autoOk = list.some((a) => a.agent_id === "auto-router" && a.actions.direct);
    if (sel.mode === "auto" && !autoOk) {
      const first = list.find((a) => a.kind === "specialist" && a.actions.direct);
      return { ...sel, mode: "direct", agentId: first?.agent_id ?? null, callId };
    }
    return { ...sel, caseId: thread.data?.case_id ?? sel.caseId, callId };
  }, [sel, agents.data, thread.data, callId]);

  const suggestions = useMemo(() => {
    const tick = (cases.data ?? []).flatMap((c) => c.tickers);
    const t1 = tick[0] ?? "NWSC";
    const t2 = tick[1] ?? t1;
    return [
      { title: `${t1} thesis and key risks`, prompt: `Summarise our ${t1} thesis, target and the key risks.` },
      { title: `Chart ${t1} EPS scenarios`, prompt: `Chart the ${t1} EPS scenarios (bear, base, bull) and what drives each.` },
      { title: `Latest ${t2} filing`, prompt: `Review the latest ${t2} 8-K filing. What changed and does it matter for us?` },
      { title: `5-slide deck on ${t1}`, prompt: `Make a 5-slide PowerPoint on our ${t1} thesis.` },
    ];
  }, [cases.data]);
  const initialMessages = useMemo(() => hydrate(thread.data?.messages ?? []), [thread.data?.messages]);

  if (!agents.data || !cases.data || (threadId && !thread.data && !thread.error)) return <div className="h-svh" />;
  if (thread.error) {
    return <div className="grid h-svh place-items-center text-sm text-muted-foreground">This chat isn&apos;t available in this pod.</div>;
  }

  return (
    <div className="flex h-svh flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b px-3 pl-12 md:pl-3">
        <AgentSwitcher agents={agents.data} sel={effectiveSel} setSel={setSel} />
        <CasePicker cases={cases.data} sel={effectiveSel} setSel={setSel} locked={!!threadId} />
      </header>
      <div className={cn("min-h-0 flex-1", activeCall && "pb-[81px]")}>
        <EvidenceProvider>
        <CallComposerContext.Provider value={{ callId, setCallId: setCallOverride }}>
        <PodosRuntimeProvider
          pod={pod}
          threadId={threadId}
          initialMessages={initialMessages}
          activeRunId={thread.data?.active_run?.run_id}
          selection={effectiveSel}
          suggestions={suggestions}
          onThreadCreated={(id) => {
            onCreated(id);
            void mutate([`/v1/pods/${pod}/threads`, pod]);
          }}
        >
          {prefill && <PrefillComposer text={prefill} onDone={() => setPrefill(null)} />}
          <Thread components={{ Welcome, Footnote, Composer: CallComposer, UserContext: UserCallReference, ToolFallback: PodosToolPart, ToolGroup: ActivityGroup }} />
        </PodosRuntimeProvider>
        </CallComposerContext.Provider>
        </EvidenceProvider>
      </div>
    </div>
  );
}

/** "Ask about this" from Today: put the suggested question in the composer for the user to edit or send. */
function PrefillComposer({ text, onDone }: { text: string; onDone: () => void }) {
  const aui = useAui();
  useEffect(() => {
    aui.composer.setText(text);
    onDone();
  }, [aui, text, onDone]);
  return null;
}

"use client";

import { useEffect, useMemo, useState } from "react";
import { useSWRConfig } from "swr";
import { ArchiveIcon, BadgeCheckIcon, BellIcon, ClockIcon, FileCheckIcon, FileTextIcon, InboxIcon, TriangleAlertIcon } from "lucide-react";
import { toast } from "sonner";
import { FileCard } from "@/components/ask/parts";
import { ActivityPanel } from "@/components/inbox/activity-panel";
import { MonitorsPanel } from "@/components/inbox/monitors-panel";
import { agentName, Badge, Card, Segmented, StateBadge, Tile, type Tone } from "@/components/common";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { DocumentSheet } from "@/components/vault/document-sheet";
import { Button } from "@/components/ui/button";
import { Empty as UIEmpty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useIsMobile } from "@/hooks/use-mobile";
import { api, explain, relTime, shortDate } from "@/lib/api";
import type { ArtifactRef, WorkItem } from "@/lib/types";
import { cn } from "@/lib/utils";

const CAT: Record<string, { icon: typeof BellIcon; tone: Tone; label: string }> = {
  approval: { icon: BadgeCheckIcon, tone: "blue", label: "Approval" },
  alert: { icon: BellIcon, tone: "amber", label: "Alert" },
  report: { icon: FileCheckIcon, tone: "green", label: "Report" },
  exception: { icon: TriangleAlertIcon, tone: "red", label: "Issue" },
};
const FROM: Record<string, string> = { "catalyst-monitor": "Catalyst Monitor", "podos-worker": "PodOS" };

function useNow(intervalMs = 30000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function Deadline({ deadline }: { deadline: string | null }) {
  const now = useNow();
  if (!deadline) return null;
  const hours = (new Date(deadline).getTime() - now) / 3.6e6;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs", hours < 12 ? "text-destructive" : "text-muted-foreground")}>
      <ClockIcon className="size-3" /> {hours <= 0 ? "Expired" : `${Math.floor(hours)}h to decide`}
    </span>
  );
}

function Slides({ spec }: { spec: NonNullable<WorkItem["payload"]["document_spec"]> }) {
  return (
    <div className="flex snap-x gap-3 overflow-x-auto pb-1">
      <div className="flex aspect-[16/10] w-52 shrink-0 snap-start flex-col justify-end rounded-md border border-blue-300 bg-blue-100 p-3">
        <div className="text-sm font-semibold leading-tight text-blue-1000">{spec.title}</div>
        <div className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">{spec.subtitle}</div>
      </div>
      {(spec.slides ?? []).map((s, i) => (
        <div key={i} className="aspect-[16/10] w-52 shrink-0 snap-start overflow-hidden rounded-md border bg-background p-3">
          <div className="text-xs font-semibold">{s.title}</div>
          <ul className="mt-1.5 space-y-1">
            {s.bullets.slice(0, 4).map((b, j) => (
              <li key={j} className="line-clamp-2 text-[10px] leading-snug text-muted-foreground">{b}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function Detail({ item, onChanged }: { item: WorkItem; onChanged: () => void }) {
  const { pod } = usePod();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [doc, setDoc] = useState<string | null>(null);
  // One idempotency key per opened decision: double clicks and retries replay safely.
  const idemKey = useMemo(() => `${item.work_item_id}:${item.version}:${crypto.randomUUID()}`, [item.work_item_id, item.version]);
  const files = usePodData<ArtifactRef[]>(item.evidence_artifact_id ? `/v1/pods/${pod}/artifacts` : null);
  const file = files.data?.find((a) => a.artifact_id === item.evidence_artifact_id);
  const pending = item.kind === "actionable" && item.state === "WAITING_FOR_HUMAN";

  async function decide(decision: "approve" | "reject" | "request_changes") {
    setBusy(true);
    try {
      await api(`/v1/work-items/${item.work_item_id}/decisions`, {
        pod, body: { decision, expected_version: item.version, idempotency_key: idemKey, comment },
      });
      toast.success(decision === "approve" ? "Approved. Generating the brief now." : "Decision recorded");
    } catch (e) {
      toast.error(explain(e));
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  async function setState(action: string, extra: Record<string, unknown> = {}) {
    await api(`/v1/work-items/${item.work_item_id}/state`, { pod, body: { action, ...extra } });
    onChanged();
  }

  return (
    <div className="mx-auto max-w-3xl px-5 pb-8 pt-14 md:p-8">
      <Card className="p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-2">
          <Badge tone={(CAT[item.category] ?? CAT.alert).tone}>{(CAT[item.category] ?? CAT.alert).label}</Badge>
          {FROM[item.source_agent_id ?? ""] ?? agentName(item.source_agent_id)} · {relTime(item.created_at)}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={() => setState("snooze", { snooze_hours: 4 })} className="h-7 gap-1 rounded-md px-2"><ClockIcon className="size-3.5" /> Snooze</Button>
          <Button variant="ghost" size="sm" onClick={() => setState(item.archived_at ? "unarchive" : "archive")} className="h-7 gap-1 rounded-md px-2"><ArchiveIcon className="size-3.5" /> {item.archived_at ? "Unarchive" : "Archive"}</Button>
        </div>
      </div>
      <h1 className="mt-3 text-xl font-semibold tracking-tight">{item.title}</h1>
      <div className="mt-2 flex items-center gap-3">
        <StateBadge state={item.state} />
        {pending && <Deadline deadline={item.deadline_at} />}
        {item.priority === "high" && <Badge tone="red">High priority</Badge>}
      </div>
      {item.summary && <p className="mt-4 text-[15px] leading-7">{item.summary}</p>}

      {item.payload.evidence && item.payload.evidence.length > 0 && (
        <div className="mt-6">
          <div className="mb-2 text-xs font-medium text-muted-foreground">Evidence</div>
          {item.payload.evidence.map((e) => (
            <Button key={e.doc_id} variant="ghost" onClick={() => setDoc(e.doc_id)} className="h-auto w-full justify-start gap-2.5 rounded-md border px-3 py-2.5 text-left text-sm hover:bg-gray-100">
              <Tile tone="blue" size="sm"><FileTextIcon /></Tile>
              <span className="flex-1 truncate">{e.title}</span>
              <span className="text-xs text-muted-foreground">{shortDate(e.published_at)}</span>
            </Button>
          ))}
        </div>
      )}

      {item.payload.document_spec && (
        <div className="mt-6">
          <div className="mb-2 text-xs font-medium text-muted-foreground">
            Draft brief · generated only if approved
          </div>
          <Slides spec={item.payload.document_spec} />
        </div>
      )}

      {file && <FileCard artifact={file} className="mt-6" />}

      {pending && (
        <div className="mt-8 rounded-lg border bg-canvas p-4">
          <Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a comment (optional)" rows={2} className="resize-none bg-background" />
          <div className="mt-3 flex items-center justify-between">
            <span className="text-xs text-muted-foreground">Recorded with your name in the audit trail</span>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" className="h-8 rounded-md text-destructive hover:text-destructive" disabled={busy} onClick={() => decide("reject")}>Reject</Button>
              <Button variant="outline" size="sm" className="h-8 rounded-md" disabled={busy} onClick={() => decide("request_changes")}>Request changes</Button>
              <Button size="sm" className="h-8" disabled={busy} onClick={() => decide("approve")}>Approve</Button>
            </div>
          </div>
        </div>
      )}
      {(item.state === "APPROVED" || item.state === "RUNNING") && (
        <p className="mt-6 text-sm text-muted-foreground"><span className="shimmer">Approved by {item.resolved_by}. Generating the brief…</span></p>
      )}
      {item.resolved_by && !["APPROVED", "RUNNING"].includes(item.state) && (
        <p className="mt-6 text-xs text-muted-foreground">Decided by {item.resolved_by}</p>
      )}
      </Card>
      <DocumentSheet docId={doc} onClose={() => setDoc(null)} />
    </div>
  );
}

type Tab = "inbox" | "monitors" | "activity";

export default function InboxPage() {
  const { pod } = usePod();
  const { mutate: globalMutate } = useSWRConfig();
  const isMobile = useIsMobile();
  // Deep links from Today: /<pod>/inbox?item=<id>, /<pod>/inbox?tab=monitors&new=1. Pod pages only render client-side, after the session loads.
  const [tab, setTab] = useState<Tab>(() => {
    const t = typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("tab");
    return t === "monitors" || t === "activity" ? t : "inbox";
  });
  const [view, setView] = useState<"inbox" | "actionable" | "archived">("inbox");
  const { data, mutate } = usePodData<WorkItem[]>(`/v1/pods/${pod}/work-items?view=${view}`, { refreshInterval: 3000 });
  const [picked, setPicked] = useState<string | null>(() =>
    typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("item"));
  const items = useMemo(() => data ?? [], [data]);
  const selected = picked ?? items[0]?.work_item_id ?? null;
  const current = items.find((i) => i.work_item_id === selected) ?? null;

  useEffect(() => {
    if (current && !current.read_at) {
      void api(`/v1/work-items/${current.work_item_id}/state`, { pod, body: { action: "read" } }).then(() => {
        void mutate();
        void globalMutate([`/v1/pods/${pod}/inbox/summary`, pod]);
      });
    }
  }, [current, pod, mutate, globalMutate]);

  return (
    <div className="flex h-svh flex-col">
      <div className="flex h-14 shrink-0 items-center border-b px-4 pl-12 md:pl-4">
        <Segmented
          value={tab}
          onChange={(next) => { setTab(next); setPicked(null); }}
          options={[{ value: "inbox", label: "Inbox" }, { value: "monitors", label: "Monitors" }, { value: "activity", label: "Activity" }]}
        />
      </div>
      {tab === "inbox" ? (
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[360px_1fr]">
      <aside className="flex min-h-0 flex-col border-r">
        <div className="flex h-12 shrink-0 items-center justify-end px-4">
          <Segmented
            value={view}
            onChange={(v) => { setView(v); setPicked(null); }}
            options={[{ value: "inbox", label: "All" }, { value: "actionable", label: "To decide" }, { value: "archived", label: "Archived" }]}
          />
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto" role="listbox" aria-label="Inbox items">
          {items.length === 0 && (
            <li>
              <UIEmpty className="py-16">
                <EmptyHeader>
                  <EmptyMedia variant="icon"><InboxIcon /></EmptyMedia>
                  <EmptyTitle>Nothing here</EmptyTitle>
                  <EmptyDescription>Monitors send alerts and approvals to this inbox.</EmptyDescription>
                </EmptyHeader>
              </UIEmpty>
            </li>
          )}
          {items.map((i) => {
            const cat = CAT[i.category] ?? CAT.alert;
            const Icon = cat.icon;
            const active = selected === i.work_item_id;
            return (
              <li key={i.work_item_id} className="px-2">
                <Button
                  role="option"
                  aria-selected={active}
                  variant="ghost"
                  onClick={() => setPicked(i.work_item_id)}
                  className={cn("h-auto w-full justify-start gap-3 rounded-md px-2.5 py-2.5 text-left", active ? "bg-gray-100" : "hover:bg-gray-100/60")}
                >
                  <Tile tone={cat.tone} size="sm"><Icon /></Tile>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className={cn("truncate text-sm", !i.read_at ? "font-semibold" : "text-foreground/90")}>{i.title}</span>
                      <span className="ml-auto shrink-0 text-xs text-muted-foreground">{relTime(i.created_at)}</span>
                    </span>
                    <span className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{i.summary}</span>
                  </span>
                  {!i.read_at && <span className="mt-1.5 size-2 shrink-0 rounded-full bg-blue-700" aria-label="Unread" />}
                </Button>
              </li>
            );
          })}
        </ul>
      </aside>
      <section className="hidden min-h-0 overflow-y-auto bg-canvas md:block">
        {current ? (
          <Detail key={`${current.work_item_id}-${current.version}`} item={current} onChanged={() => void mutate()} />
        ) : (
          <div className="grid h-full place-items-center text-sm text-muted-foreground">Select an item</div>
        )}
      </section>
      {isMobile && (
        <Sheet open={!!picked && !!current} onOpenChange={(open) => { if (!open) setPicked(null); }}>
          <SheetContent side="right" className="w-full max-w-full p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-xl">
            <SheetTitle className="sr-only">{current?.title ?? "Inbox item"}</SheetTitle>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {current && <Detail key={`${current.work_item_id}-${current.version}`} item={current} onChanged={() => void mutate()} />}
            </div>
          </SheetContent>
        </Sheet>
      )}
      </div>
      ) : tab === "monitors" ? (
        <div className="min-h-0 flex-1"><MonitorsPanel /></div>
      ) : (
        <div className="min-h-0 flex-1"><ActivityPanel /></div>
      )}
    </div>
  );
}

"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { AgentTile, agentName, Badge, Card, Empty, Segmented, StateBadge, type Tone } from "@/components/common";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useIsMobile } from "@/hooks/use-mobile";
import { api, explain, relTime } from "@/lib/api";
import type { RunRow } from "@/lib/types";

const MODE: Record<string, { label: string; tone: Tone }> = {
  direct: { label: "Chat", tone: "blue" },
  auto: { label: "Auto", tone: "gray" },
  scheduled: { label: "Monitor", tone: "amber" },
  background: { label: "Background", tone: "amber" },
};

export function ActivityPanel() {
  const { pod } = usePod();
  const isMobile = useIsMobile();
  const [scope, setScope] = useState<"mine" | "pod">("mine");
  const { data, mutate } = usePodData<RunRow[]>(`/v1/pods/${pod}/runs?scope=${scope}`, { refreshInterval: 3000 });
  const count = (s: string) => (data ?? []).filter((r) => r.state === s).length;
  const canStop = (r: RunRow) => (r.state === "RUNNING" || r.state === "QUEUED") && (r.mode === "direct" || r.mode === "auto");

  async function stop(r: RunRow) {
    try {
      await api(`/v1/runs/${r.run_id}/cancel`, { pod, method: "POST" });
      await mutate();
    } catch (e) {
      toast.error(explain(e));
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[1200px] px-5 py-8 md:px-10 md:py-10">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-2xl text-sm text-muted-foreground">Every agent run you started, plus this pod&apos;s background monitor runs.</p>
          <Segmented value={scope} onChange={setScope} options={[{ value: "mine", label: "Mine" }, { value: "pod", label: "Include monitors" }]} />
        </div>
        <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          {([["Completed", "COMPLETED", "text-green-900"], ["Running", "RUNNING", "text-amber-900"], ["Needs approval", "WAITING_FOR_HUMAN", "text-blue-900"], ["Failed", "FAILED", "text-red-900"]] as const).map(([label, s, color]) => (
            <Card key={s} className="px-5 py-4">
              <div className="text-[13px] text-muted-foreground">{label}</div>
              <div className={`mt-1 text-2xl font-semibold tabular-nums tracking-tight ${count(s) ? color : ""}`}>{count(s)}</div>
            </Card>
          ))}
        </div>
        {data?.length === 0 ? (
          <Empty title="Nothing here yet">Start a chat or run a monitor.</Empty>
        ) : isMobile ? (
          <div className="grid gap-3">
            {(data ?? []).map((r) => {
              const mode = MODE[r.mode] ?? { label: r.mode, tone: "gray" as Tone };
              return (
                <Card key={r.run_id} className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      {r.thread_id ? (
                        <Link href={`/${pod}/ask/${r.thread_id}`} className="line-clamp-2 font-medium hover:underline">{r.thread_title ?? "Chat"}</Link>
                      ) : (
                        <span className="line-clamp-2 font-medium">{r.monitor_name ?? "Background run"}</span>
                      )}
                      {r.error && <div className="mt-1 text-xs text-red-900">{r.error.message ?? "Something went wrong"}</div>}
                    </div>
                    <span className="shrink-0 pt-0.5 text-xs text-muted-foreground">{relTime(r.created_at)}</span>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 border-t pt-3">
                    <span className="flex min-w-0 items-center gap-1.5">
                      <AgentTile agentId={r.agent_id} size="sm" />
                      <span className="truncate text-[13px]">{agentName(r.agent_id)}</span>
                    </span>
                    <Badge tone={mode.tone}>{mode.label}</Badge>
                    <StateBadge state={r.state} />
                    {canStop(r) && (
                      <Button
                        variant="link"
                        className="ml-auto h-auto p-0 text-xs font-normal text-muted-foreground hover:text-foreground hover:underline"
                        onClick={() => stop(r)}
                      >
                        Stop
                      </Button>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        ) : (
          <Card>
            <Table className="text-sm">
              <TableHeader>
                <TableRow className="bg-canvas hover:bg-canvas">
                  <TableHead className="h-auto px-4 py-2.5 text-xs font-medium">Work</TableHead>
                  <TableHead className="h-auto px-4 py-2.5 text-xs font-medium">Agent</TableHead>
                  <TableHead className="h-auto px-4 py-2.5 text-xs font-medium">Type</TableHead>
                  <TableHead className="h-auto px-4 py-2.5 text-xs font-medium">Status</TableHead>
                  <TableHead className="h-auto px-4 py-2.5 text-right text-xs font-medium">Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data ?? []).map((r) => {
                  const mode = MODE[r.mode] ?? { label: r.mode, tone: "gray" as Tone };
                  return (
                    <TableRow key={r.run_id} className="hover:bg-gray-100/60">
                      <TableCell className="max-w-96 px-4 py-3">
                        {r.thread_id ? (
                          <Link href={`/${pod}/ask/${r.thread_id}`} className="line-clamp-1 font-medium hover:underline">{r.thread_title ?? "Chat"}</Link>
                        ) : (
                          <span className="line-clamp-1 font-medium">{r.monitor_name ?? "Background run"}</span>
                        )}
                        {r.error && <div className="text-xs text-red-900">{r.error.message ?? "Something went wrong"}</div>}
                      </TableCell>
                      <TableCell className="px-4 py-3 whitespace-nowrap">
                        <span className="flex items-center gap-2">
                          <AgentTile agentId={r.agent_id} size="sm" />
                          <span className="text-[13px]">{agentName(r.agent_id)}</span>
                        </span>
                      </TableCell>
                      <TableCell className="px-4 py-3"><Badge tone={mode.tone}>{mode.label}</Badge></TableCell>
                      <TableCell className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <StateBadge state={r.state} />
                          {canStop(r) && (
                            <Button
                              variant="link"
                              className="h-auto p-0 text-xs font-normal text-muted-foreground hover:text-foreground hover:underline"
                              onClick={() => stop(r)}
                            >
                              Stop
                            </Button>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="px-4 py-3 text-right whitespace-nowrap text-muted-foreground">{relTime(r.created_at)}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Card>
        )}
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { MoreHorizontalIcon, PlusIcon, RadarIcon } from "lucide-react";
import { toast } from "sonner";
import { AgentTile, agentName, Badge, personName, Card, Empty, StateBadge } from "@/components/common";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, explain, relTime } from "@/lib/api";
import type { Monitor } from "@/lib/types";

const every = (s: number) => (s < 3600 ? `Every ${Math.round(s / 60)} min` : `Every ${Math.round(s / 3600)} h`);

export function MonitorsPanel() {
  const { pod, info } = usePod();
  const { data, mutate } = usePodData<Monitor[]>(`/v1/pods/${pod}/monitors`, { refreshInterval: 3000 });
  // Deep link from Today (/<pod>/inbox?tab=monitors&new=1) opens the create dialog once.
  const [create, setCreate] = useState(() => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("new") === "1");
  const [form, setForm] = useState({ name: "", tickers: "", keywords: "guidance, resign, export control", minutes: "60" });

  useEffect(() => {
    if (!create) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("new");
    window.history.replaceState(null, "", url.pathname + url.search);
  }, [create]);

  async function act(fn: () => Promise<unknown>, ok?: string) {
    try {
      await fn();
      if (ok) toast.success(ok);
      await mutate();
    } catch (e) {
      toast.error(explain(e));
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[1200px] px-5 py-8 md:px-10 md:py-10">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-2xl text-sm text-muted-foreground">
            Background watchlists for {info.display_name}. They run the moment a filing lands for a ticker they watch (and on a schedule as a backstop), whether or not anyone is signed in.
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <Button size="sm" variant="outline" className="h-8 rounded-md" onClick={() => act(async () => {
              const f = await api<{ ticker: string; form: string }>(`/v1/pods/${pod}/simulate/filing`, { method: "POST" });
              toast.success(`${f.ticker} filed an ${f.form} with the SEC (mock). Every pod watching ${f.ticker} gets it within seconds.`);
            })}>
              Simulate SEC filing
            </Button>
            <Button size="sm" className="h-8 rounded-md" onClick={() => setCreate(true)}><PlusIcon /> New monitor</Button>
          </div>
        </div>
        {data?.length === 0 && <Empty title="No monitors yet" icon={<RadarIcon className="size-5 text-gray-700" />}>Add a watchlist to have PodOS keep an eye on your tickers for you.</Empty>}
        <div className="grid gap-4">
          {(data ?? []).map((m) => (
            <Card key={m.monitor_id} className="p-5">
              <div className="flex flex-wrap items-start gap-4">
                <AgentTile agentId={m.agent_id} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[15px] font-semibold">{m.name}</span>
                    {m.enabled ? <Badge tone="green">Active</Badge> : <Badge>Paused</Badge>}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {m.configuration.tickers.map((t) => <Badge key={t} tone="gray">{t}</Badge>)}
                    {m.configuration.keywords.map((k) => <Badge key={k}>{k}</Badge>)}
                  </div>
                </div>
                <div className="flex w-full items-center justify-end gap-2 sm:w-auto">
                  <Button size="sm" variant="outline" className="h-8 rounded-md" disabled={!m.enabled}
                    onClick={() => act(() => api(`/v1/pods/${pod}/monitors/${m.monitor_id}/run`, { method: "POST" }), "Run started. Anything it finds lands in your Inbox")}>
                    Run now
                  </Button>
                  <Switch
                    checked={m.enabled}
                    onCheckedChange={(v) => act(() => api(`/v1/pods/${pod}/monitors/${m.monitor_id}`, { method: "PATCH", body: { enabled: v } }), v ? "Monitor resumed" : "Monitor paused")}
                    aria-label={m.enabled ? "Pause monitor" : "Resume monitor"}
                  />
                  <DropdownMenu>
                    <DropdownMenuTrigger render={<Button variant="ghost" size="icon" className="text-muted-foreground hover:bg-gray-100" aria-label="More" />}>
                      <MoreHorizontalIcon className="size-4" />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-56">
                      <DropdownMenuItem disabled>Approver: {personName(m.configuration.approval_recipient)}</DropdownMenuItem>
                      <DropdownMenuItem disabled>Notifies: {m.configuration.notify.map(personName).join(", ")}</DropdownMenuItem>
                      <DropdownMenuItem disabled>Run by {agentName(m.agent_id)}</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </div>
              <div className="mt-5 grid grid-cols-2 gap-4 border-t pt-4 sm:grid-cols-4">
                <div><div className="text-xs text-muted-foreground">Schedule</div><div className="mt-0.5 text-sm">{every(m.schedule_seconds)}</div></div>
                <div><div className="text-xs text-muted-foreground">Last run</div><div className="mt-0.5 text-sm">{relTime(m.last_run_at)}</div></div>
                <div><div className="text-xs text-muted-foreground">Status</div><div className="mt-0.5">{m.last_run_state ? (
                  <span className="flex flex-wrap items-center gap-2">
                    <StateBadge state={m.last_run_state} />
                    {m.last_trigger === "event" && <Badge tone="purple">Triggered by a filing</Badge>}
                  </span>
                ) : <span className="text-sm text-muted-foreground">Not run yet</span>}</div></div>
                <div><div className="text-xs text-muted-foreground">Approver</div><div className="mt-0.5 text-sm">{personName(m.configuration.approval_recipient)}</div></div>
              </div>
            </Card>
          ))}
        </div>
      </div>
      <Dialog open={create} onOpenChange={setCreate}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New monitor</DialogTitle>
            <DialogDescription>Runs with your access, re-checked every time it runs.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <FieldGroup className="gap-4">
              <Field><FieldLabel htmlFor="m-name">Name</FieldLabel><Input id="m-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Semis filings watch" /></Field>
              <Field><FieldLabel htmlFor="m-t">Tickers</FieldLabel><Input id="m-t" value={form.tickers} onChange={(e) => setForm({ ...form, tickers: e.target.value })} placeholder="NWSC, LGS" /></Field>
              <Field><FieldLabel htmlFor="m-k">Keywords</FieldLabel><Input id="m-k" value={form.keywords} onChange={(e) => setForm({ ...form, keywords: e.target.value })} /></Field>
              <Field><FieldLabel htmlFor="m-s">Run every (minutes)</FieldLabel><Input id="m-s" type="number" min={1} value={form.minutes} onChange={(e) => setForm({ ...form, minutes: e.target.value })} /></Field>
            </FieldGroup>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreate(false)}>Cancel</Button>
            <Button disabled={form.name.length < 3 || !form.tickers} onClick={() => act(async () => {
              await api(`/v1/pods/${pod}/monitors`, { body: {
                name: form.name, tickers: form.tickers.split(",").map((s) => s.trim()).filter(Boolean),
                keywords: form.keywords.split(",").map((s) => s.trim()).filter(Boolean), schedule_seconds: Math.max(1, Number(form.minutes)) * 60,
              } });
              setCreate(false);
            }, "Monitor created")}>Create</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

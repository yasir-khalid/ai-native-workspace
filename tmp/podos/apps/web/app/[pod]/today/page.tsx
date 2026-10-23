"use client";

import { prepareAsk } from "@/lib/ask-handoff";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  BellIcon, BadgeCheckIcon, FileCheckIcon, FileTextIcon, HourglassIcon, MessageSquareIcon, NewspaperIcon, PlusIcon,
  RefreshCwIcon, SquarePenIcon, SunIcon, XIcon,
} from "lucide-react";
import { Badge, Card, Dot, Empty, Page, PageHeader, Tile, type Tone } from "@/components/common";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { DocumentSheet } from "@/components/vault/document-sheet";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, relTime } from "@/lib/api";

type Item = {
  key: string; kind: string; title: string; why: string; detail?: string; at: string | null; href?: string;
  ticker?: string | null; source?: string; doc_id?: string; ask?: string; priority?: string; yours?: boolean;
};
type Briefing = { watched_tickers?: string[]; generated_at: string; summary: string; sections: { id: string; title: string; items: Item[] }[] };

const KIND: Record<string, { icon: typeof BellIcon; tone: Tone; label: string }> = {
  approval: { icon: BadgeCheckIcon, tone: "blue", label: "Approval" },
  work: { icon: BellIcon, tone: "amber", label: "To do" },
  filing: { icon: FileTextIcon, tone: "purple", label: "Filing" },
  news: { icon: NewspaperIcon, tone: "teal", label: "News" },
  transcript: { icon: FileTextIcon, tone: "purple", label: "Transcript" },
  report: { icon: FileCheckIcon, tone: "green", label: "Ready" },
  stale: { icon: HourglassIcon, tone: "gray", label: "Stale" },
};

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function TodayPage() {
  const { pod, me } = usePod();
  const router = useRouter();
  const { data, error, mutate, isValidating } = usePodData<Briefing>(`/v1/pods/${pod}/briefing`, { refreshInterval: 30000 });
  const [doc, setDoc] = useState<string | null>(null);
  const first = me.display_name.split(" ")[0];

  const feedback = (item: Item, action: "opened" | "asked" | "dismissed") =>
    api(`/v1/pods/${pod}/briefing/feedback`, { body: { item_key: item.key, action, ticker: item.ticker ?? null } }).catch(() => {});

  async function dismiss(item: Item) {
    await mutate((b) => b && { ...b, sections: b.sections.map((s) => ({ ...s, items: s.items.filter((i) => i.key !== item.key) })) }, { revalidate: false });
    await feedback(item, "dismissed");
    void mutate();
  }

  function ask(item: Item) {
    void feedback(item, "asked");
    router.push(prepareAsk(pod, { prompt: item.ask }));
  }

  const disabled = error instanceof ApiError && error.status === 403;

  return (
    <Page
      header={
        <PageHeader
          title={`${greeting()}, ${first}`}
          description={data?.summary ?? (disabled ? "The Today briefing isn't enabled for you in this pod." : "Pulling together what changed…")}
          actions={
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              {data && <span>Updated {relTime(data.generated_at)}</span>}
              <Button size="sm" variant="outline" className="h-8 rounded-md" onClick={() => void mutate()} disabled={isValidating}>
                <RefreshCwIcon className={isValidating ? "animate-spin motion-reduce:animate-none" : ""} /> Refresh
              </Button>
              <Button size="sm" className="h-8 rounded-md" nativeButton={false} render={<Link href={`/${pod}/ask`} />}>
                <SquarePenIcon /> New chat
              </Button>
            </div>
          }
        />
      }
    >
      {data && !disabled && (
        <section aria-label="Briefing overview" className="mb-10 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {([{ id: "waiting", label: "Waiting on you", tone: "blue" }, { id: "changed", label: "New developments", tone: "purple" }, { id: "finished", label: "Ready to review", tone: "green" }] as const).map((metric) => (
            <Card key={metric.id} className="px-5 py-4">
              <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Dot tone={metric.tone} />{metric.label}</p>
              <p className="mt-2 text-[28px] leading-none font-bold tracking-[-0.025em] tabular-nums">{data.sections.find((section) => section.id === metric.id)?.items.length ?? 0}</p>
            </Card>
          ))}
        </section>
      )}
      <div className="grid items-start gap-10 xl:grid-cols-[minmax(0,1fr)_250px]">
      <div>
      {disabled ? (
        <Empty title="Today isn't available" icon={<SunIcon className="size-5 text-gray-700" />}>Ask your platform team to enable it for this pod.</Empty>
      ) : !data ? (
        <div className="grid gap-6">{[0, 1].map((i) => <Skeleton key={i} className="h-44 rounded-lg" />)}</div>
      ) : data.sections.length === 0 ? (
        <Empty title="You're all caught up" icon={<SunIcon className="size-5 text-amber-900" />}>
          <span>Nothing new since yesterday. Your monitors are watching and will bring anything important here.</span>
          <div className="mt-4">
            <Button size="sm" className="h-8 rounded-md" nativeButton={false} render={<Link href={`/${pod}/ask`} />}>
              <SquarePenIcon /> Start a new chat
            </Button>
          </div>
        </Empty>
      ) : (
        <div className="grid gap-10">
          {data.sections.map((s) => (
            <section key={s.id} aria-labelledby={`today-${s.id}`}>
              <div className="mb-4 flex items-baseline gap-3">
                <h2 id={`today-${s.id}`} className="text-base font-semibold tracking-[-0.01em]">{s.title}</h2>
                <Badge>{s.items.length}</Badge>
              </div>
              <Card className="divide-y">
                {s.items.map((item) => {
                  const k = KIND[item.kind] ?? KIND.work;
                  const Icon = k.icon;
                  const open = item.href ? `/${pod}/${item.href}` : null;
                  return (
                    <div key={item.key} className="group flex items-start gap-3 px-4 py-6 md:gap-4 md:px-6">
                      <Tile tone={k.tone}><Icon /></Tile>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge tone={k.tone}>{k.label}</Badge>
                          {item.ticker && <Badge tone="gray">{item.ticker}</Badge>}
                          {item.priority === "high" && <Badge tone="red">High priority</Badge>}
                          {item.at && <span className="text-xs text-muted-foreground">{relTime(item.at)}</span>}
                        </div>
                        <p className="mt-3 text-[17px] leading-snug font-normal text-foreground">{item.title}</p>
                        <p className="mt-0.5 text-sm text-muted-foreground">
                          {item.why}{item.source ? ` · ${item.source}` : ""}
                        </p>
                        {item.detail && <p className="mt-1.5 line-clamp-2 text-sm text-gray-900">{item.detail}</p>}
                        <div className="mt-3 flex flex-wrap gap-2">
                          {open && (
                            <Button size="sm" variant={item.kind === "approval" ? "default" : "outline"} className="h-8 rounded-md" nativeButton={false}
                              render={<Link href={open} onClick={() => void feedback(item, "opened")} />}>
                              {item.kind === "approval" ? "Review" : item.kind === "stale" ? "Open case" : "Open"}
                            </Button>
                          )}
                          {item.doc_id && (
                            <Button size="sm" variant="outline" className="h-8 rounded-md" onClick={() => { void feedback(item, "opened"); setDoc(item.doc_id!); }}>
                              Read
                            </Button>
                          )}
                          {item.ask && (
                            <Button size="sm" variant="ghost" className="h-8 rounded-md" onClick={() => ask(item)}>
                              <MessageSquareIcon /> Ask about this
                            </Button>
                          )}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => void dismiss(item)}
                        className="size-7 shrink-0 text-muted-foreground opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100 hover:bg-gray-100 hover:text-foreground focus-visible:opacity-100"
                        aria-label={`Dismiss ${item.title}`}
                      >
                        <XIcon className="size-4" />
                      </Button>
                    </div>
                  );
                })}
              </Card>
            </section>
          ))}
        </div>
      )}
      </div>
      <aside>
        {data?.watched_tickers?.length ? (
          <Card className="p-5">
            <h2 className="text-sm font-semibold">Your coverage</h2>
            <p className="mt-1 text-xs text-muted-foreground">Tickers you own or follow. New filings for them land here first.</p>
            <div className="mt-4 flex flex-wrap gap-1.5">{data.watched_tickers.map((ticker) => <Badge key={ticker} tone="gray">{ticker}</Badge>)}</div>
            <Link href={`/${pod}/inbox?tab=monitors`} className="mt-5 inline-flex items-center gap-1 text-sm font-medium text-link hover:underline">Manage monitors <span aria-hidden>→</span></Link>
          </Card>
        ) : (
          <Card className="p-5">
            <h2 className="text-sm font-semibold">Set up your coverage</h2>
            <p className="mt-1 text-xs text-muted-foreground">A one-time setup. Pick the companies you follow and PodOS watches their filings, news and guidance for you — no need to check back.</p>
            <Button size="sm" className="mt-4 h-8 rounded-md" nativeButton={false} render={<Link href={`/${pod}/inbox?tab=monitors&new=1`} />}>
              <PlusIcon /> Set up coverage
            </Button>
          </Card>
        )}
      </aside>
      </div>
      <DocumentSheet docId={doc} onClose={() => setDoc(null)} />
    </Page>
  );
}

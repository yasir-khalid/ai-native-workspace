"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowUpRightIcon, HeadphonesIcon, PlayIcon, SearchIcon, XIcon } from "lucide-react";
import { Badge, Card, Empty, Page, PageHeader, Segmented } from "@/components/common";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { useCallPlayer } from "@/components/calls/calls-player";
import { Cover } from "@/components/calls/cover";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Skeleton } from "@/components/ui/skeleton";
import {
  callKind, compactNumber, formatCallDate, formatClock, type CallSummary,
} from "@/lib/calls";
import { cn } from "@/lib/utils";

type KindFilter = "all" | "interviews" | "earnings";

function CallGridCard({ call, playing }: { call: CallSummary; playing: boolean }) {
  const { pod } = usePod();
  const { playCall } = useCallPlayer();
  const kind = callKind(call.event_title);
  return (
    <Card className="group relative h-full p-4 transition-[border-color,box-shadow] hover:border-gray-300 hover:shadow-sm">
      <Link
        href={`/${pod}/calls/${call.call_id}`}
        className="absolute inset-0 z-0 rounded-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none"
        aria-label={`Open ${call.event_title}`}
      />
      <div className="pointer-events-none relative z-10 flex items-start gap-3">
        <button
          type="button"
          onClick={() => playCall(call, 0)}
          className="pointer-events-auto shrink-0 rounded-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none"
          aria-label={`Play ${call.event_title}`}
        >
          <Cover ticker={call.ticker} callId={call.call_id} label={kind.toUpperCase()} size="md" playing={playing} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Badge tone={kind === "Earnings" ? "purple" : "teal"}>{kind}</Badge>
            <span className="truncate font-mono text-[10px] text-muted-foreground">{call.ticker}</span>
            <ArrowUpRightIcon className="ml-auto size-3.5 shrink-0 text-subtle opacity-0 transition-opacity duration-150 group-hover:opacity-100 motion-reduce:transition-none" />
          </div>
          <p className="mt-1 truncate text-[12px] text-muted-foreground">{call.company}</p>
        </div>
      </div>
      <div className="pointer-events-none relative z-10 mt-3">
        <h2 className="line-clamp-2 text-[14px] leading-5 font-semibold tracking-[-0.01em]">{call.event_title}</h2>
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          {formatCallDate(call.event_date, true)} · {formatClock(call.duration)} · {compactNumber(call.asr.word_count)} words
        </p>
        <div className="mt-2.5 flex flex-wrap gap-1">
          {call.tags.slice(0, 3).map((tag) => <Badge key={tag} tone="gray" className="max-w-full"><span className="truncate">{tag}</span></Badge>)}
        </div>
      </div>
    </Card>
  );
}

export default function CallsPage() {
  const { pod } = usePod();
  const { data, error } = usePodData<CallSummary[]>(`/v1/pods/${pod}/calls`);
  const { current, playing, playCall } = useCallPlayer();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<KindFilter>("all");

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data ?? []).filter((call) => {
      const matchesKind = kind === "all"
        || (kind === "interviews" && callKind(call.event_title) === "Interview")
        || (kind === "earnings" && callKind(call.event_title) === "Earnings");
      if (!matchesKind) return false;
      if (!needle) return true;
      return [call.company, call.ticker, call.event_title, call.summary_short, ...call.tags]
        .some((value) => value.toLowerCase().includes(needle));
    });
  }, [data, kind, query]);

  const featured = filtered[0];
  const rest = filtered.slice(1);
  const totalSeconds = filtered.reduce((sum, call) => sum + call.duration, 0);
  const totalWords = filtered.reduce((sum, call) => sum + call.asr.word_count, 0);

  return (
    <Page
      header={
        <PageHeader
          eyebrow="Audio library"
          title="Calls"
          description="Earnings calls and interviews, transcribed locally with chapters, Q&A and citations."
          actionsClassName="w-full sm:w-auto"
          actions={
            <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center">
              <InputGroup className="h-9 w-full bg-background sm:w-64">
                <InputGroupAddon><SearchIcon className="size-4" /></InputGroupAddon>
                <InputGroupInput
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search calls"
                  aria-label="Search calls"
                  className="text-[13px]"
                />
                {query && (
                  <InputGroupAddon align="inline-end">
                    <InputGroupButton size="icon-xs" aria-label="Clear search" onClick={() => setQuery("")}>
                      <XIcon className="size-3.5" />
                    </InputGroupButton>
                  </InputGroupAddon>
                )}
              </InputGroup>
              <Segmented
                value={kind}
                onChange={setKind}
                options={[
                  { value: "all", label: "All" },
                  { value: "interviews", label: "Interviews" },
                  { value: "earnings", label: "Earnings" },
                ]}
              />
            </div>
          }
        />
      }
    >
      <div className={cn(current && "pb-24")}>
        <p className="mb-5 text-[11px] text-muted-foreground">
          {filtered.length} {filtered.length === 1 ? "call" : "calls"} · {Math.round(totalSeconds / 60)} min · {compactNumber(totalWords)} words transcribed
        </p>
        {error ? (
          <p className="py-10 text-center text-sm text-muted-foreground">The calls library isn&apos;t available in this pod.</p>
        ) : !data ? (
          <div className="grid gap-4">
            <Skeleton className="h-64 rounded-lg" />
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {[0, 1, 2].map((item) => <Skeleton key={item} className="h-44 rounded-lg" />)}
            </div>
          </div>
        ) : filtered.length === 0 ? (
          <Empty title="No calls match" icon={<HeadphonesIcon className="size-5 text-muted-foreground" />}>
            Try a different search or filter.
          </Empty>
        ) : (
          <div className="grid gap-6">
            <Card className="relative overflow-hidden p-5 md:p-6">
              <div className="grid gap-5 md:grid-cols-[auto_minmax(0,1fr)] md:items-center md:gap-8">
                <button
                  type="button"
                  onClick={() => playCall(featured, 0)}
                  className="w-fit rounded-2xl shadow-[0_18px_50px_-24px_color-mix(in_oklab,var(--foreground)_45%,transparent)] focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none"
                  aria-label={`Play ${featured.event_title}`}
                >
                  <Cover
                    ticker={featured.ticker}
                    callId={featured.call_id}
                    label={callKind(featured.event_title).toUpperCase()}
                    size="xl"
                    playing={current?.call_id === featured.call_id && playing}
                  />
                </button>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="purple">Latest</Badge>
                    <Badge tone={callKind(featured.event_title) === "Earnings" ? "purple" : "teal"}>{callKind(featured.event_title)}</Badge>
                    <span className="font-mono text-[10px] text-muted-foreground">{featured.exchange}: {featured.ticker}</span>
                  </div>
                  <div className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <h2 className="text-[22px] leading-7 font-bold tracking-[-0.03em] md:text-[26px] md:leading-8">{featured.company}</h2>
                    <span className="text-[12px] text-muted-foreground">{formatCallDate(featured.event_date, true)} · {formatClock(featured.duration)}</span>
                  </div>
                  <h3 className="mt-2 max-w-2xl text-[16px] leading-6 font-semibold text-balance">{featured.event_title}</h3>
                  <p className="mt-2 line-clamp-3 max-w-3xl text-[13px] leading-6 text-gray-900">{featured.summary_short}</p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {featured.tags.slice(0, 3).map((tag) => <Badge key={tag} tone="gray">{tag}</Badge>)}
                  </div>
                  <div className="mt-4 flex flex-wrap items-center gap-2">
                    <Button size="sm" className="h-8 rounded-md" onClick={() => playCall(featured, 0)}>
                      <PlayIcon /> Play
                    </Button>
                    <Button size="sm" variant="outline" className="h-8 rounded-md" nativeButton={false} render={<Link href={`/${pod}/calls/${featured.call_id}`} />}>
                      Open
                    </Button>
                  </div>
                </div>
              </div>
            </Card>

            {rest.length > 0 && (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {rest.map((call) => (
                  <CallGridCard key={call.call_id} call={call} playing={current?.call_id === call.call_id && playing} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </Page>
  );
}

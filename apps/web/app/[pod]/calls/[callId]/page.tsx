"use client";

import { prepareAsk } from "@/lib/ask-handoff";

import Link from "next/link";
import { Suspense, useEffect, useRef, useState, type CSSProperties } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  ChevronLeftIcon, DownloadIcon, MessageSquareIcon, PauseIcon, PlayIcon,
} from "lucide-react";
import { Badge, Page } from "@/components/common";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { useCallPlayer } from "@/components/calls/calls-player";
import { callPalette, Cover } from "@/components/calls/cover";
import { ChaptersPanel } from "@/components/calls/chapters-panel";
import { InsightsPanel } from "@/components/calls/insights-panel";
import { QAPanel } from "@/components/calls/qa-panel";
import { TranscriptPanel } from "@/components/calls/transcript-panel";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { API_URL } from "@/lib/api";
import {
  asrModelLabel, callKind, formatCallDate, formatClock, type CallDetail,
} from "@/lib/calls";
import { cn } from "@/lib/utils";

type CallTab = "transcript" | "chapters" | "qa" | "insights";

function CallLoading() {
  return (
    <Page>
      <div className="grid gap-6 md:grid-cols-[auto_minmax(0,1fr)] md:items-end">
        <Skeleton className="size-48 rounded-2xl md:size-64" />
        <div className="grid gap-3">
          <Skeleton className="h-4 w-36" />
          <Skeleton className="h-9 w-full max-w-xl" />
          <Skeleton className="h-4 w-72" />
          <Skeleton className="h-8 w-80" />
        </div>
      </div>
      <Skeleton className="mt-10 h-72 rounded-lg" />
    </Page>
  );
}

function CallView() {
  const { callId } = useParams<{ callId: string }>();
  const { pod } = usePod();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data, error } = usePodData<CallDetail>(`/v1/pods/${pod}/calls/${callId}`);
  const { current, playing, seek, toggle, playCall, setCallChapters } = useCallPlayer();
  const [tab, setTab] = useState<CallTab>("transcript");
  const deepLinkRef = useRef<string | null>(null);
  const focusSentence = searchParams.get("s");

  const isCurrent = current?.call_id === data?.call_id;

  useEffect(() => {
    if (!data || current?.call_id !== data.call_id) return;
    setCallChapters(data.chapters);
  }, [current?.call_id, data, setCallChapters]);

  useEffect(() => {
    if (!data) return;
    const raw = searchParams.get("t");
    const sentence = focusSentence ? data.transcript.paragraphs.flatMap((p) => p.sentences).find((s) => s.id === focusSentence) : null;
    const key = `${data.call_id}:${raw}:${focusSentence}`;
    if (deepLinkRef.current === key) return;
    if (raw !== null || sentence) {
      const seconds = raw !== null ? Number(raw) : sentence!.start;
      if (Number.isFinite(seconds) && seconds >= 0) {
        deepLinkRef.current = key;
        if (current?.call_id === data.call_id) seek(seconds);
        else playCall(data, seconds);
      }
    }
  }, [current?.call_id, data, focusSentence, playCall, searchParams, seek]);

  function selectTab(next: CallTab) {
    setTab(next);
  }

  function openCallChat() {
    if (!data) return;
    const question = `What were the three biggest takeaways from the ${data.company} (${data.ticker}) call "${data.event_title}" on ${formatCallDate(data.event_date, true)}?`;
    router.push(prepareAsk(pod, { mode: "auto", callId: data.call_id, prompt: question }));
  }

  if (error) {
    return (
      <Page>
        <div className="grid min-h-[50vh] place-items-center text-center">
          <div>
            <p className="text-base font-medium">This call isn&apos;t available in this pod.</p>
            <Button className="mt-4 h-8 rounded-md" nativeButton={false} render={<Link href={`/${pod}/calls`} />}>
              <ChevronLeftIcon /> All calls
            </Button>
          </div>
        </div>
      </Page>
    );
  }

  if (!data) return <CallLoading />;
  const call = data;
  const [a, b] = callPalette(`${call.ticker}-${call.call_id}`);
  const kind = callKind(call.event_title);
  const asrLabel = `Local · ${asrModelLabel(call.asr.model)}`;
  const backdrop = {
    "--call-a": a,
    "--call-b": b,
    background:
      "radial-gradient(65% 140% at 3% 0%, color-mix(in oklab, var(--call-a) 19%, transparent), transparent 68%), radial-gradient(48% 110% at 82% 0%, color-mix(in oklab, var(--call-b) 13%, transparent), transparent 64%)",
  } as CSSProperties;

  return (
    <Page
      header={
        <header className="relative overflow-hidden border-b bg-background">
          <div aria-hidden className="pointer-events-none absolute inset-0" style={backdrop} />
          <div className="relative mx-auto w-full max-w-[1200px] px-5 pt-14 pb-8 md:px-10 md:pt-10">
            <Link
              href={`/${pod}/calls`}
              className="inline-flex items-center gap-1 rounded text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              <ChevronLeftIcon className="size-3.5" /> All calls
            </Link>
            <div className="mt-5 grid gap-6 md:grid-cols-[auto_minmax(0,1fr)] md:items-end md:gap-8">
              <Cover
                ticker={call.ticker}
                callId={call.call_id}
                label={kind.toUpperCase()}
                size="detail"
                playing={isCurrent && playing}
                className="shadow-[0_22px_60px_-24px_color-mix(in_oklab,var(--foreground)_50%,transparent)]"
              />
              <div className="min-w-0">
                <p className="field-label">{kind} · {formatCallDate(call.event_date, true)}</p>
                <h1 className="mt-2 max-w-3xl text-[28px] leading-9 font-bold tracking-[-0.035em] text-balance md:text-[34px] md:leading-10">{call.event_title}</h1>
                <p className="mt-2 text-[14px] text-muted-foreground">
                  <span className="font-medium text-foreground">{call.company}</span> · <span className="font-mono text-[12px]">{call.exchange}: {call.ticker}</span>
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[11px] text-muted-foreground">
                  <span>{formatClock(call.duration)}</span>
                  <span aria-hidden>·</span>
                  <span>{call.chapters.length} chapters</span>
                  <span aria-hidden>·</span>
                  <span>{call.qa.length} questions</span>
                  <Badge tone="purple" className="ml-1">{asrLabel}</Badge>
                  <Badge tone={call.asr.wer_vs_reference !== undefined && call.asr.wer_vs_reference <= 0.1 ? "green" : "amber"}>
                    WER {call.asr.wer_vs_reference === undefined ? "—" : `${(call.asr.wer_vs_reference * 100).toFixed(1)}%`}
                  </Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {call.tags.map((tag, index) => (
                    <Badge key={tag} tone="gray" className={cn(index >= 4 && "max-sm:hidden")}>{tag}</Badge>
                  ))}
                  {call.tags.length > 4 && (
                    <Badge tone="gray" className="sm:hidden">+{call.tags.length - 4}</Badge>
                  )}
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    className="h-8 rounded-md"
                    onClick={() => {
                      if (isCurrent) toggle();
                      else playCall(call, 0);
                    }}
                  >
                    {isCurrent && playing ? <PauseIcon /> : <PlayIcon />}
                    {isCurrent && playing ? "Pause" : "Play"}
                  </Button>
                  <Button size="sm" variant="outline" className="h-8 rounded-md" onClick={() => openCallChat()}>
                    <MessageSquareIcon /> Ask this call
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    className="size-8 rounded-md"
                    nativeButton={false}
                    render={<a href={`${API_URL}${call.audio_url}`} download aria-label="Download call audio" />}
                  >
                    <DownloadIcon />
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </header>
      }
    >
      <div className={cn("grid gap-8", current && "pb-24")}>
        <div className="min-w-0">
          <Tabs value={tab} onValueChange={(value) => selectTab(value as CallTab)} className="gap-0">
            <div className="overflow-x-auto border-b">
              <TabsList variant="line" className="h-10 w-max min-w-full justify-start gap-0 rounded-none bg-transparent p-0">
                <TabsTrigger value="transcript" className="h-10 flex-none px-3 text-[12px] after:bottom-0">Transcript</TabsTrigger>
                <TabsTrigger value="chapters" className="h-10 flex-none px-3 text-[12px] after:bottom-0">Chapters {call.chapters.length}</TabsTrigger>
                <TabsTrigger value="qa" className="h-10 flex-none px-3 text-[12px] after:bottom-0">Q&amp;A {call.qa.length}</TabsTrigger>
                <TabsTrigger value="insights" className="h-10 flex-none px-3 text-[12px] after:bottom-0">Insights</TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="transcript" className="mt-5">
              <TranscriptPanel call={call} active={tab === "transcript"} focusSentence={focusSentence} />
            </TabsContent>
            <TabsContent value="chapters" className="mt-5">
              <ChaptersPanel call={call} />
            </TabsContent>
            <TabsContent value="qa" className="mt-5">
              <QAPanel call={call} />
            </TabsContent>
            <TabsContent value="insights" className="mt-5">
              <InsightsPanel call={call} />
            </TabsContent>
          </Tabs>
        </div>
      </div>

    </Page>
  );
}

export default function CallPage() {
  return <Suspense fallback={<CallLoading />}><CallView /></Suspense>;
}

"use client";

import { prepareAsk } from "@/lib/ask-handoff";

import Link from "next/link";
import { Suspense, useEffect, useRef, useState, type CSSProperties } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  ChevronLeftIcon, DownloadIcon, MessageSquareIcon, PauseIcon, PlayIcon,
} from "lucide-react";
import { Page } from "@/components/common";
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
    <div className="call-experience min-w-0 bg-background pb-32" data-call-experience>
      <header className="relative isolate border-b bg-background" data-call-hero>
        <div aria-hidden className="pointer-events-none absolute inset-0" style={backdrop} />
        <div className="relative w-full px-5 pt-14 pb-7 sm:px-8 md:pt-7 2xl:px-12">
          <Link href={`/${pod}/calls`} className="inline-flex items-center gap-1 rounded text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
            <ChevronLeftIcon className="size-4" /> All calls
          </Link>
          <div className="mt-5 grid grid-cols-[auto_minmax(0,1fr)] items-start gap-5 sm:gap-7">
            <Cover ticker={call.ticker} callId={call.call_id} label={kind.toUpperCase()} size="episode" playing={isCurrent && playing} className="shadow-lg" />
            <div className="min-w-0 self-center sm:hidden">
              <p className="text-xs text-muted-foreground">{kind} · {formatCallDate(call.event_date, true)}</p>
              <p className="mt-2 text-sm font-semibold">{call.company}</p>
              <p className="mt-1 text-xs text-muted-foreground">{Math.round(call.duration / 60)} min · {call.chapters.length} chapters</p>
            </div>
            <div className="col-span-2 min-w-0 sm:col-span-1">
              <p className="hidden text-sm font-medium text-muted-foreground sm:block">{kind} · {formatCallDate(call.event_date, true)}</p>
              <h1 className="mt-2 text-[23px] leading-[1.15] font-bold tracking-[-0.035em] text-balance sm:text-[29px] xl:text-[36px]">{call.event_title}</h1>
              <p className="mt-3 hidden text-sm text-muted-foreground sm:block"><span className="font-semibold text-foreground">{call.company}</span> · {call.exchange}: {call.ticker}</p>
              <p className="mt-2 hidden text-sm text-muted-foreground sm:block">{Math.round(call.duration / 60)} min · {call.chapters.length} chapters · {call.qa.length} questions</p>
              <div className="mt-5 flex flex-wrap items-center gap-2.5">
                <Button className="rounded-full" onClick={() => { if (isCurrent) toggle(); else playCall(call, 0); }}>
                  {isCurrent && playing ? <PauseIcon data-icon="inline-start" fill="currentColor" /> : <PlayIcon data-icon="inline-start" fill="currentColor" />}
                  {isCurrent && playing ? "Pause" : "Play call"}
                </Button>
                <Button variant="outline" className="rounded-full" onClick={openCallChat}><MessageSquareIcon data-icon="inline-start" /> Ask this call</Button>
                <Button variant="ghost" size="icon" className="rounded-full" nativeButton={false} render={<a href={`${API_URL}${call.audio_url}`} download aria-label="Download call audio" />}><DownloadIcon /></Button>
                <details className="ml-auto min-w-0 max-w-full text-xs text-muted-foreground">
                  <summary className="cursor-pointer rounded px-2 py-1">Transcript details</summary>
                  <div className="mt-2 flex max-w-lg flex-col gap-1 rounded-lg border bg-background p-3">
                    <span>Machine transcript · {asrLabel}</span>
                    <span>Reference WER: {call.asr.wer_vs_reference === undefined ? "Not measured" : `${(call.asr.wer_vs_reference * 100).toFixed(1)}%`}</span>
                    <span>Duration: {formatClock(call.duration)} · Topics: {call.tags.join(", ")}</span>
                  </div>
                </details>
              </div>
            </div>
          </div>
        </div>
      </header>
      <div className="w-full min-w-0 px-5 pt-3 sm:px-8 2xl:px-12">
        <Tabs value={tab} onValueChange={value => selectTab(value as CallTab)} className="gap-0">
          <div className="call-tab-strip border-b">
            <TabsList variant="line" className="min-w-full justify-start gap-3 rounded-none p-0 sm:gap-5">
              <TabsTrigger value="transcript" className="flex-none px-1 after:bottom-0">Transcript</TabsTrigger>
              <TabsTrigger value="chapters" className="flex-none px-1 after:bottom-0">Chapters <span className="text-xs text-muted-foreground">{call.chapters.length}</span></TabsTrigger>
              <TabsTrigger value="qa" className="flex-none px-1 after:bottom-0">Q&amp;A <span className="text-xs text-muted-foreground">{call.qa.length}</span></TabsTrigger>
              <TabsTrigger value="insights" className="flex-none px-1 after:bottom-0">Insights</TabsTrigger>
            </TabsList>
          </div>
          <TabsContent value="transcript" className="mt-5"><TranscriptPanel call={call} active={tab === "transcript"} focusSentence={focusSentence} /></TabsContent>
          <TabsContent value="chapters" className="mt-6"><ChaptersPanel call={call} /></TabsContent>
          <TabsContent value="qa" className="mt-6"><QAPanel call={call} /></TabsContent>
          <TabsContent value="insights" className="mt-6"><InsightsPanel call={call} /></TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

export default function CallPage() {
  return <Suspense fallback={<CallLoading />}><CallView /></Suspense>;
}

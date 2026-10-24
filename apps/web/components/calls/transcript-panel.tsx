"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { AlignLeftIcon, ArrowDownIcon, ArrowUpIcon, AudioLinesIcon, CheckIcon, ChevronRightIcon, ListIcon, PlayIcon, SearchIcon, XIcon } from "lucide-react";
import { useCallPlayer } from "@/components/calls/calls-player";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatClock, type CallDetail, type Sentence } from "@/lib/calls";
import { transcriptBlocks } from "@/lib/transcript-reader";
import { cn } from "@/lib/utils";

function Highlighted({ text, query }: { text: string; query: string }) {
  const trimmed = query.trim();
  if (!trimmed) return <>{text}</>;
  const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return <>{text.split(new RegExp(`(${escaped})`, "ig")).map((part, index) => part.toLowerCase() === trimmed.toLowerCase()
    ? <mark key={index} className="rounded-sm bg-accent px-0.5 text-accent-foreground">{part}</mark>
    : <Fragment key={index}>{part}</Fragment>)}</>;
}

function SpeakerPortrait({ name, index }: { name: string; index: number }) {
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word[0]).join("");
  return <Avatar aria-hidden className="size-7 shrink-0"><AvatarFallback className={cn("text-[10px] font-semibold", index === 0 && "bg-secondary text-secondary-foreground")}>{initials}</AvatarFallback></Avatar>;
}

export function TranscriptPanel({ call, active, focusSentence }: { call: CallDetail; active: boolean; focusSentence: string | null }) {
  const { current, playing, time, seek, toggle, playCall } = useCallPlayer();
  const [query, setQuery] = useState("");
  const [speakerFilter, setSpeakerFilter] = useState("all");
  const [view, setView] = useState("reader");
  const [follow, setFollow] = useState(true);
  const [matchIndex, setMatchIndex] = useState(0);
  const isCurrent = current?.call_id === call.call_id;
  const doc = call.transcript;
  const trimmed = query.trim();
  const sentences = useMemo(() => doc.paragraphs.flatMap((paragraph) => paragraph.sentences), [doc.paragraphs]);
  const blocks = useMemo(() => transcriptBlocks(doc.paragraphs, call.chapters, view === "lines"), [doc.paragraphs, call.chapters, view]);
  const speakers = useMemo(() => new Map(doc.speakers.map((speaker, index) => [speaker.name, { ...speaker, index }])), [doc.speakers]);
  const sentenceSpeakers = useMemo(() => new Map(doc.paragraphs.flatMap((paragraph) => paragraph.sentences.map((sentence) => [sentence.id, paragraph.speaker] as const))), [doc.paragraphs]);
  const speakerNames = useMemo(() => [...new Set(doc.paragraphs.map((paragraph) => paragraph.speaker))], [doc.paragraphs]);
  const activeSentence = useMemo(() => isCurrent ? sentences.find((sentence) => time >= sentence.start && time < sentence.end) : null, [isCurrent, sentences, time]);
  const currentChapter = isCurrent ? call.chapters.findLast((chapter) => chapter.start <= time) : null;
  const matches = useMemo(() => trimmed ? sentences.filter((sentence) => sentence.text.toLowerCase().includes(trimmed.toLowerCase()) && (speakerFilter === "all" || sentenceSpeakers.get(sentence.id) === speakerFilter)) : [], [sentences, trimmed, speakerFilter, sentenceSpeakers]);
  const visibleBlocks = useMemo(() => blocks.filter((block) => (speakerFilter === "all" || block.speaker === speakerFilter) && (!trimmed || block.sentences.some((sentence) => sentence.text.toLowerCase().includes(trimmed.toLowerCase())))), [blocks, trimmed, speakerFilter]);

  function scrollToSentence(id: string, behavior: ScrollBehavior = "smooth") {
    document.getElementById(`sentence-${id}`)?.scrollIntoView({ block: "center", behavior });
  }

  function revealSentence(id: string) {
    setQuery("");
    setSpeakerFilter("all");
    requestAnimationFrame(() => requestAnimationFrame(() => scrollToSentence(id)));
  }

  function resumeFollowing() {
    setFollow(true);
    if (activeSentence) revealSentence(activeSentence.id);
  }

  function playAt(start: number) {
    if (window.getSelection()?.toString()) return;
    if (isCurrent) {
      seek(start);
      if (!playing) toggle();
    } else playCall(call, start);
  }

  function jumpChapter(id: string) {
    const chapter = call.chapters.find((item) => item.id === id);
    if (!chapter) return;
    setFollow(false);
    revealSentence(chapter.start_sentence);
  }

  function moveMatch(delta: number) {
    if (!matches.length) return;
    const next = (matchIndex + delta + matches.length) % matches.length;
    setMatchIndex(next);
    scrollToSentence(matches[next].id);
  }

  useEffect(() => {
    if (!active || !focusSentence) return;
    const frame = requestAnimationFrame(() => {
      setQuery("");
      setSpeakerFilter("all");
      requestAnimationFrame(() => scrollToSentence(focusSentence));
    });
    return () => cancelAnimationFrame(frame);
  }, [active, focusSentence]);

  useEffect(() => {
    if (!active || !follow || !playing || !activeSentence) return;
    const element = document.getElementById(`sentence-${activeSentence.id}`);
    if (!element) return;
    const rect = element.getBoundingClientRect();
    if (rect.top < 175 || rect.bottom > window.innerHeight - 110) element.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [active, activeSentence, follow, playing]);

  function renderSentence(sentence: Sentence) {
    const selected = activeSentence?.id === sentence.id;
    return <Fragment key={sentence.id}>
      <button type="button" id={`sentence-${sentence.id}`} onClick={() => playAt(sentence.start)}
        className={cn("inline cursor-pointer scroll-mt-44 rounded-sm px-0.5 text-left transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none", selected && "bg-secondary text-secondary-foreground", focusSentence === sentence.id && "ring-2 ring-ring")}
        aria-current={selected ? "true" : undefined} aria-label={`Play from ${formatClock(sentence.start)}: ${sentence.text}`}>
        <Highlighted text={sentence.text} query={query} />
      </button>{" "}
    </Fragment>;
  }

  return (
    <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_208px] xl:gap-10">
      <div className="min-w-0 rounded-xl border bg-card text-card-foreground" data-transcript-reader>
        <div className="sticky top-0 z-20 rounded-t-xl border-b bg-card px-4 py-3 sm:px-5">
          <div className="flex flex-wrap items-center gap-2">
            <InputGroup className="h-8 min-w-40 flex-1">
              <InputGroupAddon><SearchIcon /></InputGroupAddon>
              <InputGroupInput value={query} onChange={(event) => { setQuery(event.target.value); setMatchIndex(0); setFollow(false); }} placeholder="Find in transcript…" aria-label="Search transcript" />
              {query && <InputGroupAddon align="inline-end"><InputGroupButton size="icon-xs" aria-label="Clear transcript search" onClick={() => { setQuery(""); setMatchIndex(0); }}><XIcon /></InputGroupButton></InputGroupAddon>}
            </InputGroup>
            <Select value={speakerFilter} onValueChange={(value) => { setSpeakerFilter(value ?? "all"); setMatchIndex(0); setFollow(false); }}>
              <SelectTrigger size="sm" aria-label="Filter transcript by speaker" className="max-w-44"><SelectValue>{speakerFilter === "all" ? "All speakers" : speakerFilter}</SelectValue></SelectTrigger>
              <SelectContent><SelectGroup><SelectItem value="all">All speakers</SelectItem>{speakerNames.map((name) => <SelectItem key={name} value={name}>{name}</SelectItem>)}</SelectGroup></SelectContent>
            </Select>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
              <ToggleGroup value={[view]} onValueChange={(values) => { if (values[0]) setView(values[0]); }} size="sm" spacing={0} aria-label="Transcript layout">
                <ToggleGroupItem value="reader" aria-label="Reader view"><AlignLeftIcon /> Reader</ToggleGroupItem>
                <ToggleGroupItem value="lines" aria-label="Line by line view"><ListIcon /> Lines</ToggleGroupItem>
              </ToggleGroup>
              {trimmed && <div className="ml-1 flex items-center gap-1" aria-live="polite">
                <span className="text-[11px] text-muted-foreground tabular-nums">{matches.length ? `${matchIndex + 1} / ${matches.length}` : "No matches"}</span>
                <Button size="icon-xs" variant="ghost" disabled={!matches.length} aria-label="Previous match" onClick={() => moveMatch(-1)}><ArrowUpIcon /></Button>
                <Button size="icon-xs" variant="ghost" disabled={!matches.length} aria-label="Next match" onClick={() => moveMatch(1)}><ArrowDownIcon /></Button>
              </div>}
            </div>
            <Button type="button" variant={follow ? "secondary" : "ghost"} size="sm" aria-pressed={follow} onClick={() => follow ? setFollow(false) : resumeFollowing()}>
              {follow ? <CheckIcon /> : <AudioLinesIcon />}{follow ? "Following audio" : activeSentence ? "Jump to current" : "Follow audio"}
            </Button>
          </div>
          {isCurrent && activeSentence && <div className="mt-3 flex items-center gap-2 border-t pt-2.5 text-[11px] text-muted-foreground">
            <AudioLinesIcon className={cn("size-3.5", playing && "motion-safe:animate-pulse")} />
            <span>{playing ? "Listening" : "Paused"}</span><span aria-hidden>·</span>
            <span className="truncate font-medium text-foreground">{sentenceSpeakers.get(activeSentence.id)}</span>
            <span className="ml-auto font-mono tabular-nums">{formatClock(time)}</span>
          </div>}
        </div>

        <div className="border-b bg-muted/20 px-4 py-3 lg:hidden">
          <Select value={null} onValueChange={(value) => { if (value) jumpChapter(value); }}>
            <SelectTrigger size="sm" className="w-full" aria-label="Jump to chapter"><SelectValue placeholder={currentChapter?.title ?? "Jump to a chapter"} /></SelectTrigger>
            <SelectContent><SelectGroup>{call.chapters.map((chapter) => <SelectItem key={chapter.id} value={chapter.id}>{formatClock(chapter.start)} · {chapter.title}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
        </div>

        <div className="px-4 pb-6 sm:px-6" onWheel={() => setFollow(false)} onTouchMove={() => setFollow(false)}>
          {!visibleBlocks.length && <div className="py-16 text-center"><SearchIcon className="mx-auto mb-3 size-5 text-muted-foreground" /><p className="text-sm font-medium">No matching passages</p><p className="mt-1 text-xs text-muted-foreground">Try another phrase or choose all speakers.</p></div>}
          {visibleBlocks.map((block, index) => {
            const speaker = speakers.get(block.speaker);
            const chapter = call.chapters.find((item) => item.id === block.chapterId);
            const chapterStart = chapter && (index === 0 || visibleBlocks[index - 1].chapterId !== block.chapterId);
            const isSpeaking = block.sentences.some((sentence) => sentence.id === activeSentence?.id);
            return <Fragment key={block.id}>
              {chapterStart && <div className="flex scroll-mt-44 items-center gap-3 border-b pt-8 pb-3" id={`chapter-${chapter.id}`}>
                <span className="font-mono text-[10px] text-muted-foreground tabular-nums">{formatClock(chapter.start)}</span>
                <h3 className="text-[11px] font-semibold tracking-[0.08em] text-muted-foreground uppercase">{chapter.title}</h3>
              </div>}
              <section data-transcript-block data-word-count={block.sentences.reduce((n, sentence) => n + sentence.text.split(/\s+/).length, 0)} className={cn("group relative grid grid-cols-[38px_minmax(0,1fr)] gap-3 border-b border-border/60 py-5 last:border-b-0 sm:grid-cols-[48px_minmax(0,1fr)] sm:gap-4", isSpeaking && "before:absolute before:inset-y-5 before:-left-4 before:w-0.5 before:rounded-full before:bg-primary sm:before:-left-6")}>
                <button type="button" onClick={() => playAt(block.start)} aria-label={`Play passage at ${formatClock(block.start)}`} className="mt-1.5 self-start rounded font-mono text-[10px] text-muted-foreground tabular-nums transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">{formatClock(block.start)}</button>
                <div className="min-w-0">
                  <div className="mb-2.5 flex min-w-0 items-center gap-2.5">
                    <SpeakerPortrait name={block.speaker} index={speaker?.index ?? 1} />
                    <div className="min-w-0"><h4 className="text-[12px] leading-4 font-semibold">{block.speaker}</h4>
                      {!block.continued && speaker && <p className="mt-0.5 text-[10px] leading-4 text-muted-foreground">{[speaker.role, speaker.company].filter(Boolean).join(" · ")}</p>}
                      {block.continued && <p className="mt-0.5 text-[10px] leading-4 text-muted-foreground">Continued</p>}
                    </div>
                    {isSpeaking && <AudioLinesIcon aria-label="Current passage" className="ml-auto size-3.5 shrink-0 text-primary" />}
                    <Button variant="ghost" size="icon-xs" className="ml-auto shrink-0 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100" aria-label={`Play ${block.speaker} at ${formatClock(block.start)}`} onClick={() => playAt(block.start)}><PlayIcon /></Button>
                  </div>
                  <p className="max-w-[68ch] text-[14px] leading-[1.85] text-foreground/90">{block.sentences.map(renderSentence)}</p>
                </div>
              </section>
            </Fragment>;
          })}
          {!!visibleBlocks.length && <p className="pt-7 text-center text-[11px] text-muted-foreground">{trimmed || speakerFilter !== "all" ? `${visibleBlocks.length} matching passages` : "End of transcript"}</p>}
        </div>
      </div>

      <aside className="sticky top-5 hidden max-h-[calc(100svh-120px)] min-w-0 overflow-y-auto lg:block" aria-label="Transcript navigation">
        <div className="mb-4 flex items-center justify-between"><h3 className="text-[11px] font-semibold tracking-[0.1em] text-muted-foreground uppercase">In this call</h3><span className="font-mono text-[10px] text-muted-foreground">{formatClock(call.duration)}</span></div>
        <nav aria-label="Transcript chapters" className="grid gap-0.5 border-l">
          {call.chapters.map((chapter, index) => <button key={chapter.id} type="button" onClick={() => jumpChapter(chapter.id)} aria-current={currentChapter?.id === chapter.id ? "location" : undefined} className={cn("-ml-px flex items-baseline gap-2.5 border-l-2 border-transparent px-3 py-2.5 text-left transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none", currentChapter?.id === chapter.id && "border-primary bg-secondary/50")}>
            <span className="font-mono text-[10px] text-muted-foreground tabular-nums">{String(index + 1).padStart(2, "0")}</span>
            <span className="min-w-0"><span className="block text-[11px] leading-[1.5] font-medium">{chapter.title}</span><span className="mt-1 block font-mono text-[10px] text-muted-foreground tabular-nums">{formatClock(chapter.start)}</span></span>
          </button>)}
        </nav>
        <div className="mt-7 border-t pt-5"><h3 className="mb-3 text-[11px] font-semibold tracking-[0.1em] text-muted-foreground uppercase">Speakers</h3>
          <div className="grid gap-1">{speakerNames.map((name) => <button key={name} type="button" onClick={() => { setSpeakerFilter(speakerFilter === name ? "all" : name); setMatchIndex(0); setFollow(false); }} aria-pressed={speakerFilter === name} className="group flex items-center gap-2.5 rounded-md py-2 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
            <SpeakerPortrait name={name} index={speakers.get(name)?.index ?? 1} /><span className="min-w-0 flex-1"><span className="block text-[11px] font-medium group-hover:underline">{name}</span><span className="block truncate text-[10px] text-muted-foreground">{speakers.get(name)?.company}</span></span>{speakerFilter === name ? <CheckIcon className="size-3.5" /> : <ChevronRightIcon className="size-3.5 text-muted-foreground" />}
          </button>)}</div>
        </div>
      </aside>
    </div>
  );
}

"use client";

import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlignLeftIcon, ArrowDownIcon, ArrowUpIcon, AudioLinesIcon, CheckIcon, ChevronRightIcon, ListIcon, LocateFixedIcon, PlayIcon, SearchIcon, XIcon } from "lucide-react";
import { useCallPlayer } from "@/components/calls/calls-player";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatClock, type CallDetail } from "@/lib/calls";
import { sentenceAtTime, transcriptBlocks, type TranscriptBlock } from "@/lib/transcript-reader";
import { cn } from "@/lib/utils";

function Highlighted({ text, query }: { text: string; query: string }) {
  const trimmed = query.trim();
  if (!trimmed) return <>{text}</>;
  const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return <>{text.split(new RegExp(`(${escaped})`, "ig")).map((part, index) => part.toLowerCase() === trimmed.toLowerCase()
    ? <mark key={index} className="rounded-sm bg-accent text-accent-foreground">{part}</mark>
    : <Fragment key={index}>{part}</Fragment>)}</>;
}

function SpeakerPortrait({ name, index }: { name: string; index: number }) {
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word[0]).join("");
  return <Avatar aria-hidden className="size-8 shrink-0"><AvatarFallback className={cn("text-xs font-semibold", index === 0 && "bg-secondary text-secondary-foreground")}>{initials}</AvatarFallback></Avatar>;
}

type Speaker = CallDetail["transcript"]["speakers"][number] & { index: number };

// Audio time updates four times per second. Reconcile this large subtree only
// when the highlighted sentence, filters or transcript presentation change.
const Passages = memo(function Passages({ blocks, chapters, speakers, activeId, focusId, query, onPlay }: {
  blocks: TranscriptBlock[]; chapters: CallDetail["chapters"]; speakers: Map<string, Speaker>;
  activeId: string | null; focusId: string | null; query: string; onPlay: (time: number) => void;
}) {
  return <div className="px-1 sm:px-3" data-transcript-passages>
    {!blocks.length && <div className="py-16 text-center"><SearchIcon className="mx-auto mb-3 size-5 text-muted-foreground" /><p className="text-base font-medium">No matching passages</p><p className="mt-1 text-sm text-muted-foreground">Try another phrase or choose all speakers.</p></div>}
    {blocks.map((block, index) => {
      const speaker = speakers.get(block.speaker);
      const chapter = chapters.find((item) => item.id === block.chapterId);
      const chapterStart = chapter && (index === 0 || blocks[index - 1].chapterId !== block.chapterId);
      const isSpeaking = block.sentences.some(sentence => sentence.id === activeId);
      return <Fragment key={block.id}>
        {chapterStart && <div className="flex items-baseline gap-3 border-b pt-8 pb-4" id={`chapter-${chapter.id}`}>
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{formatClock(chapter.start)}</span>
          <h2 className="text-sm leading-5 font-semibold text-foreground">{chapter.title}</h2>
        </div>}
        <section id={`passage-${block.id}`} data-transcript-block data-active={isSpeaking || undefined}
          data-word-count={block.sentences.reduce((n, sentence) => n + sentence.text.split(/\s+/).length, 0)}
          className="transcript-passage group relative grid grid-cols-[38px_minmax(0,1fr)] gap-3 border-b border-border/60 py-7 last:border-b-0 sm:grid-cols-[48px_minmax(0,1fr)] sm:gap-5 sm:py-8">
          <button type="button" onClick={() => onPlay(block.start)} aria-label={`Play passage at ${formatClock(block.start)}`} className="mt-2 self-start rounded text-xs text-muted-foreground tabular-nums hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">{formatClock(block.start)}</button>
          <div className="min-w-0">
            <div className="mb-4 flex min-w-0 items-center gap-3">
              <SpeakerPortrait name={block.speaker} index={speaker?.index ?? 1} />
              <div className="min-w-0 flex-1"><h3 className="text-sm leading-5 font-semibold">{block.speaker}</h3>
                <p className="mt-0.5 text-xs leading-4 text-muted-foreground">{block.continued ? "Continued" : [speaker?.role, speaker?.company].filter(Boolean).join(" · ") || "Speaker"}</p>
              </div>
              <AudioLinesIcon aria-hidden className={cn("size-4 shrink-0 text-link", !isSpeaking && "invisible")} />
              <Button variant="ghost" size="icon-sm" className="shrink-0 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100" aria-label={`Play ${block.speaker} at ${formatClock(block.start)}`} onClick={() => onPlay(block.start)}><PlayIcon /></Button>
            </div>
            <p className="transcript-prose max-w-[76ch] text-base leading-[1.85] text-foreground/85 md:text-[17px] md:leading-[1.9]">
              {block.sentences.map(sentence => <Fragment key={sentence.id}>
                <span role="button" tabIndex={0} id={`sentence-${sentence.id}`} onClick={() => onPlay(sentence.start)}
                  onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); onPlay(sentence.start); } }}
                  className="transcript-sentence cursor-pointer rounded-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  data-active={sentence.id === activeId || undefined} data-cited={focusId === sentence.id || undefined}
                  aria-current={sentence.id === activeId ? "true" : undefined} aria-label={`Play from ${formatClock(sentence.start)}: ${sentence.text}`}>
                  <Highlighted text={sentence.text} query={query} />
                </span>{" "}
              </Fragment>)}
            </p>
          </div>
        </section>
      </Fragment>;
    })}
    {!!blocks.length && <p className="py-8 text-center text-xs text-muted-foreground">{query ? `${blocks.length} matching passages` : "End of transcript"}</p>}
  </div>;
});

export function TranscriptPanel({ call, active, focusSentence }: { call: CallDetail; active: boolean; focusSentence: string | null }) {
  const { current, playing, time, seek, toggle, playCall } = useCallPlayer();
  const [query, setQuery] = useState("");
  const [speakerFilter, setSpeakerFilter] = useState("all");
  const [view, setView] = useState("reader");
  const [follow, setFollow] = useState(false);
  const [matchIndex, setMatchIndex] = useState(0);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const plannedScroll = useRef<{ top: number; expires: number } | null>(null);
  const isCurrent = current?.call_id === call.call_id;
  const doc = call.transcript;
  const trimmed = query.trim();
  const sentences = useMemo(() => doc.paragraphs.flatMap(paragraph => paragraph.sentences), [doc.paragraphs]);
  const blocks = useMemo(() => transcriptBlocks(doc.paragraphs, call.chapters, view === "lines"), [doc.paragraphs, call.chapters, view]);
  const speakers = useMemo(() => new Map(doc.speakers.map((speaker, index) => [speaker.name, { ...speaker, index }])), [doc.speakers]);
  const sentenceSpeakers = useMemo(() => new Map(doc.paragraphs.flatMap(paragraph => paragraph.sentences.map(sentence => [sentence.id, paragraph.speaker] as const))), [doc.paragraphs]);
  const speakerNames = useMemo(() => [...new Set(doc.paragraphs.map(paragraph => paragraph.speaker))], [doc.paragraphs]);
  const activeSentence = isCurrent ? sentenceAtTime(sentences, time) : null;
  const activeId = activeSentence?.id ?? null;
  const activeBlockId = useMemo(() => blocks.find(block => block.sentences.some(sentence => sentence.id === activeId))?.id, [activeId, blocks]);
  const currentChapter = isCurrent ? call.chapters.findLast(chapter => chapter.start <= time) : null;
  const matches = useMemo(() => trimmed ? sentences.filter(sentence => sentence.text.toLowerCase().includes(trimmed.toLowerCase()) && (speakerFilter === "all" || sentenceSpeakers.get(sentence.id) === speakerFilter)) : [], [sentences, trimmed, speakerFilter, sentenceSpeakers]);
  const visibleBlocks = useMemo(() => blocks.filter(block => (speakerFilter === "all" || block.speaker === speakerFilter) && (!trimmed || block.sentences.some(sentence => sentence.text.toLowerCase().includes(trimmed.toLowerCase())))), [blocks, trimmed, speakerFilter]);

  const scrollToTarget = useCallback((id: string) => {
    const element = document.getElementById(id);
    if (!element) return;
    const inset = (toolbarRef.current?.offsetHeight ?? 100) + 28;
    // Scroll the document only. scrollIntoView also shifts overflow ancestors,
    // including tab strips, which caused the old reader to jump and flicker.
    const top = Math.max(0, Math.min(document.documentElement.scrollHeight - window.innerHeight,
      window.scrollY + element.getBoundingClientRect().top - inset));
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    plannedScroll.current = { top, expires: reduced ? 0 : performance.now() + 1500 };
    window.scrollTo({ top, behavior: reduced ? "instant" : "smooth" });
  }, []);

  const revealSentence = useCallback((id: string) => {
    setQuery(""); setSpeakerFilter("all");
    requestAnimationFrame(() => requestAnimationFrame(() => scrollToTarget(`sentence-${id}`)));
  }, [scrollToTarget]);

  const playAt = useCallback((start: number) => {
    if (window.getSelection()?.toString()) return;
    if (isCurrent) { seek(start); if (!playing) toggle(); }
    else playCall(call, start);
  }, [call, isCurrent, playCall, playing, seek, toggle]);

  function jumpChapter(id: string) {
    const chapter = call.chapters.find(item => item.id === id);
    if (!chapter) return;
    setFollow(false); revealSentence(chapter.start_sentence);
  }

  function moveMatch(delta: number) {
    if (!matches.length) return;
    const next = (matchIndex + delta + matches.length) % matches.length;
    setMatchIndex(next); setFollow(false); scrollToTarget(`sentence-${matches[next].id}`);
  }

  useEffect(() => {
    if (!active || !focusSentence) return;
    const frame = requestAnimationFrame(() => { setFollow(false); revealSentence(focusSentence); });
    return () => cancelAnimationFrame(frame);
  }, [active, focusSentence, revealSentence]);

  useEffect(() => {
    if (!active || !follow || !playing || !activeBlockId) return;
    const element = document.getElementById(`passage-${activeBlockId}`);
    if (!element) return;
    const rect = element.getBoundingClientRect();
    const top = (toolbarRef.current?.offsetHeight ?? 100) + 24;
    const playerHeight = document.querySelector('[aria-label="Call player"]')?.getBoundingClientRect().height ?? 0;
    const bottom = window.innerHeight - playerHeight - 32;
    // Follow at passage boundaries only, and leave a visible passage stationary.
    if (rect.bottom >= top && rect.top <= bottom) return;
    // A short speaker turn can finish while the previous smooth scroll is still
    // travelling. Don't restart it if this passage will be visible at its target.
    const pending = plannedScroll.current;
    if (pending && pending.expires > performance.now()) {
      const shift = window.scrollY - pending.top;
      if (rect.bottom + shift >= top && rect.top + shift <= bottom) return;
    }
    scrollToTarget(`passage-${activeBlockId}`);
  }, [active, activeBlockId, follow, playing, scrollToTarget]);

  useEffect(() => {
    if (!follow) return;
    const stopFollowing = () => setFollow(false);
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.target instanceof Element && event.target.closest("input, textarea, select, [role='slider'], [role='listbox'], [contenteditable='true']")) return;
      if (["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown"].includes(event.key)) stopFollowing();
    };
    window.addEventListener("wheel", stopFollowing, { passive: true });
    window.addEventListener("touchmove", stopFollowing, { passive: true });
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("wheel", stopFollowing); window.removeEventListener("touchmove", stopFollowing); window.removeEventListener("keydown", onKey); };
  }, [follow]);

  return <div className="grid items-start gap-10 xl:grid-cols-[minmax(0,1fr)_288px] 2xl:grid-cols-[minmax(0,1fr)_320px] 2xl:gap-14">
    <div className="min-w-0" data-transcript-reader>
      <div ref={toolbarRef} className="sticky top-0 z-20 border-b bg-background py-4" data-transcript-toolbar>
        <div className="flex flex-wrap items-center gap-2.5">
          <InputGroup className="min-w-40 flex-1">
            <InputGroupAddon><SearchIcon /></InputGroupAddon>
            <InputGroupInput value={query} onChange={event => { setQuery(event.target.value); setMatchIndex(0); setFollow(false); }} placeholder="Search this transcript" aria-label="Search transcript" />
            {query && <InputGroupAddon align="inline-end"><InputGroupButton size="icon-xs" aria-label="Clear transcript search" onClick={() => { setQuery(""); setMatchIndex(0); }}><XIcon /></InputGroupButton></InputGroupAddon>}
          </InputGroup>
          <Select value={speakerFilter} onValueChange={value => { setSpeakerFilter(value ?? "all"); setMatchIndex(0); setFollow(false); }}>
            <SelectTrigger aria-label="Filter transcript by speaker" className="max-w-48"><SelectValue>{speakerFilter === "all" ? "All speakers" : speakerFilter}</SelectValue></SelectTrigger>
            <SelectContent><SelectGroup><SelectItem value="all">All speakers</SelectItem>{speakerNames.map(name => <SelectItem key={name} value={name}>{name}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <ToggleGroup value={[view]} onValueChange={values => { if (values[0]) { setView(values[0]); setFollow(false); } }} size="sm" spacing={0} aria-label="Transcript layout">
            <ToggleGroupItem value="reader" aria-label="Reader view"><AlignLeftIcon /> Reader</ToggleGroupItem>
            <ToggleGroupItem value="lines" aria-label="Line by line view"><ListIcon /> Lines</ToggleGroupItem>
          </ToggleGroup>
          <div className="flex items-center gap-1.5">
            <Button type="button" variant="ghost" size="sm" disabled={!activeId} aria-label="Jump to current passage" onClick={() => { if (activeId) revealSentence(activeId); }}><LocateFixedIcon /> <span className="hidden sm:inline">Jump to current</span></Button>
            <Button type="button" variant={follow ? "secondary" : "outline"} size="sm" aria-pressed={follow} onClick={() => { setFollow(value => !value); if (!follow) { setQuery(""); setSpeakerFilter("all"); } }}>
              {follow ? <CheckIcon /> : <AudioLinesIcon />}{follow ? "Following audio" : "Follow audio"}
            </Button>
          </div>
        </div>
        <div className="mt-3 flex h-6 min-w-0 items-center gap-2 text-xs text-muted-foreground" data-transcript-status>
          {trimmed ? <><SearchIcon className="size-3.5 shrink-0" /><span aria-live="polite">{matches.length ? `${matchIndex + 1} of ${matches.length} matches` : "No matches"}</span><Button size="icon-xs" variant="ghost" disabled={!matches.length} aria-label="Previous match" onClick={() => moveMatch(-1)}><ArrowUpIcon /></Button><Button size="icon-xs" variant="ghost" disabled={!matches.length} aria-label="Next match" onClick={() => moveMatch(1)}><ArrowDownIcon /></Button></>
            : <><AudioLinesIcon className="size-3.5 shrink-0" /><span className="shrink-0">{isCurrent ? playing ? "Now playing" : "Paused" : "Read along or select a passage to listen"}</span>{isCurrent && <span className="truncate">· {activeId ? sentenceSpeakers.get(activeId) : call.company}</span>}</>}
        </div>
      </div>
      <div className="border-b py-4 xl:hidden">
        <Select value={null} onValueChange={value => { if (value) jumpChapter(value); }}>
          <SelectTrigger className="w-full" aria-label="Jump to chapter"><SelectValue placeholder={currentChapter?.title ?? "Browse chapters"} /></SelectTrigger>
          <SelectContent><SelectGroup>{call.chapters.map(chapter => <SelectItem key={chapter.id} value={chapter.id}>{formatClock(chapter.start)} · {chapter.title}</SelectItem>)}</SelectGroup></SelectContent>
        </Select>
      </div>
      <Passages blocks={visibleBlocks} chapters={call.chapters} speakers={speakers} activeId={activeId} focusId={focusSentence} query={trimmed} onPlay={playAt} />
    </div>
    <aside className="hidden min-w-0 rounded-2xl border bg-muted/25 p-5 xl:block 2xl:p-6" aria-label="Transcript navigation">
      <div className="mb-5 flex items-baseline justify-between gap-3"><h2 className="text-lg font-semibold">In this call</h2><span className="text-xs text-muted-foreground tabular-nums">{call.chapters.length} chapters</span></div>
      <nav aria-label="Transcript chapters" className="flex flex-col gap-1">
        {call.chapters.map((chapter, index) => <button key={chapter.id} type="button" onClick={() => jumpChapter(chapter.id)} aria-current={currentChapter?.id === chapter.id ? "location" : undefined} className={cn("flex items-start gap-3 rounded-lg px-3 py-3 text-left hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none", currentChapter?.id === chapter.id && "bg-accent")}>
          <span className="pt-0.5 text-xs text-muted-foreground tabular-nums">{String(index + 1).padStart(2, "0")}</span>
          <span className="min-w-0"><span className="block text-sm leading-5 font-medium">{chapter.title}</span><span className="mt-1 block text-xs text-muted-foreground tabular-nums">{formatClock(chapter.start)}</span></span>
        </button>)}
      </nav>
      <div className="mt-6 border-t pt-5"><h3 className="mb-3 text-sm font-semibold">Voices</h3>
        <div className="flex flex-col gap-2">{speakerNames.map(name => <button key={name} type="button" onClick={() => { setSpeakerFilter(speakerFilter === name ? "all" : name); setMatchIndex(0); setFollow(false); }} aria-pressed={speakerFilter === name} className="group flex items-center gap-3 rounded-lg py-2 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
          <SpeakerPortrait name={name} index={speakers.get(name)?.index ?? 1} /><span className="min-w-0 flex-1"><span className="block text-sm font-medium group-hover:underline">{name}</span><span className="block text-xs text-muted-foreground">{speakers.get(name)?.company}</span></span>{speakerFilter === name ? <CheckIcon className="size-4" /> : <ChevronRightIcon className="size-4 text-muted-foreground" />}
        </button>)}</div>
      </div>
    </aside>
  </div>;
}

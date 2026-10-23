"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowUpRightIcon, AudioLinesIcon, ChevronLeftIcon, ChevronRightIcon, FileTextIcon, PlayIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Spinner } from "@/components/ui/spinner";
import { useIsMobile } from "@/hooks/use-mobile";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { useCallPlayer } from "@/components/calls/calls-player";
import { DocumentSheet } from "@/components/vault/document-sheet";
import { callCitationHref, formatClock, type CallDetail } from "@/lib/calls";
import { shortDate } from "@/lib/api";
import type { Citation, ResearchDocument } from "@/lib/types";

const EvidenceContext = createContext<(citation: Citation, sources: Citation[]) => void>(() => {});
export const useEvidence = () => useContext(EvidenceContext);
type Selection = { citation: Citation; sources: Citation[] };

/** Source snapshots stay beside the answer; the original remains one explicit click away. */
export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [selection, setSelection] = useState<Selection | null>(null);
  const mobile = useIsMobile();
  const trigger = useRef<HTMLElement | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const open = useCallback((citation: Citation, sources: Citation[]) => {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setSelection({ citation, sources });
  }, []);
  const close = useCallback(() => { setSelection(null); trigger.current?.focus(); }, []);
  useEffect(() => {
    if (!selection || mobile) return;
    heading.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); close(); } };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [selection, mobile, close]);
  const body = selection && <EvidenceBody key={`${selection.citation.ref}-${selection.citation.doc_id}`} {...selection} select={citation => setSelection({ ...selection, citation })} />;
  return <EvidenceContext.Provider value={open}>
    <div className="flex h-full min-h-0 min-w-0">
      <div className="min-w-0 flex-1">{children}</div>
      {!mobile && selection && <aside aria-label="Source evidence" className="flex w-[360px] shrink-0 flex-col border-l bg-background lg:w-[400px]">
        <header className="flex h-14 shrink-0 items-center justify-between border-b px-5">
          <h2 ref={heading} tabIndex={-1} className="text-sm font-semibold outline-none">Source evidence</h2>
          <Button variant="ghost" size="icon-sm" onClick={close} aria-label="Close evidence"><XIcon /></Button>
        </header>
        {body}
      </aside>}
    </div>
    {mobile && <Sheet open={!!selection} onOpenChange={isOpen => { if (!isOpen) close(); }}>
      <SheetContent className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-md">
        <SheetHeader className="border-b pr-12"><SheetTitle>Source evidence</SheetTitle><SheetDescription>Inspect the passage cited in this answer.</SheetDescription></SheetHeader>
        {body}
      </SheetContent>
    </Sheet>}
  </EvidenceContext.Provider>;
}

function EvidenceBody({ citation: c, sources, select }: Selection & { select: (citation: Citation) => void }) {
  const { pod } = usePod();
  const player = useCallPlayer();
  const call = usePodData<CallDetail>(c.call_id ? `/v1/pods/${pod}/calls/${encodeURIComponent(c.call_id)}` : null);
  const doc = usePodData<ResearchDocument>(!c.call_id && !c.external && c.doc_id ? `/v1/pods/${pod}/documents/${encodeURIComponent(c.doc_id)}` : null);
  const [documentOpen, setDocumentOpen] = useState(false);
  const index = sources.findIndex(item => item.ref === c.ref);
  const audioChanged = !!(c.audio_sha256 && call.data?.audio_sha256 && c.audio_sha256 !== call.data.audio_sha256);
  const paragraphs = call.data?.transcript.paragraphs ?? [];
  const paragraph = paragraphs.find(p => p.sentences.some(s => s.id === c.sentence_id));
  const sentenceIndex = paragraph?.sentences.findIndex(s => s.id === c.sentence_id) ?? -1;
  const nearby = paragraph?.sentences.slice(Math.max(0, sentenceIndex - 1), sentenceIndex + 2) ?? [];
  const currentSentence = nearby.find(s => s.id === c.sentence_id);
  const changed = currentSentence && c.text && currentSentence.text !== c.text;
  const excerpt = c.text || c.detail || doc.data?.body;
  return <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-5">
    <div className="mb-5 flex items-center justify-between gap-2">
      <Badge variant="secondary" className="gap-1.5">{c.call_id ? <AudioLinesIcon data-icon="inline-start" /> : <FileTextIcon data-icon="inline-start" />}{c.call_id ? "Transcript" : c.external ? "External source" : c.kind === "filing" ? "Filing" : "Document"}</Badge>
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Button variant="ghost" size="icon-xs" aria-label="Previous source" disabled={index <= 0} onClick={() => select(sources[index - 1])}><ChevronLeftIcon /></Button>
        <span>Source {c.ref}</span>
        <Button variant="ghost" size="icon-xs" aria-label="Next source" disabled={index < 0 || index >= sources.length - 1} onClick={() => select(sources[index + 1])}><ChevronRightIcon /></Button>
      </div>
    </div>
    <h3 className="text-lg leading-snug font-semibold tracking-tight">{c.title}</h3>
    <p className="mt-2 text-xs text-muted-foreground">{[c.ticker, c.source, shortDate(c.published_at)].filter(Boolean).join(" · ")}</p>
    {(c.speaker || c.start !== undefined) && <div className="mt-5 border-t pt-4">
      {c.speaker && <p className="text-sm font-medium">{c.speaker}</p>}
      {c.role && <p className="mt-0.5 text-xs text-muted-foreground">{c.role}</p>}
      {c.start !== undefined && <p className="mt-1 font-mono text-xs text-muted-foreground">{formatClock(c.start)}{c.end !== undefined ? ` – ${formatClock(c.end)}` : ""}</p>}
    </div>}
    <div className="mt-5">
      <p className="mb-2 text-xs font-medium text-muted-foreground">{c.text ? "Passage saved with this answer" : "Source excerpt"}</p>
      {excerpt ? <blockquote className="border-l-2 border-blue-500 bg-blue-50/60 py-3 pr-3 pl-4 text-sm leading-7 whitespace-pre-wrap">{excerpt}</blockquote>
        : <p className="text-sm text-muted-foreground">{doc.isLoading ? "Loading source…" : "An excerpt wasn’t saved with this answer. Open the source to inspect it."}</p>}
    </div>
    {c.call_id && <p className="mt-3 text-xs leading-5 text-muted-foreground">Machine transcript · audio not independently reviewed</p>}
    <div className="mt-5 flex flex-wrap gap-2">
      {c.call_id && <>
        <Button size="sm" variant="outline" disabled={!call.data || c.start === undefined || audioChanged} onClick={() => { if (call.data) player.playCall(call.data, c.start); }}><PlayIcon data-icon="inline-start" />Listen from {formatClock(c.start ?? 0)}</Button>
        <Button size="sm" variant="ghost" nativeButton={false} render={<Link href={callCitationHref(pod, { ...c, call_id: c.call_id })} />}>Open transcript<ArrowUpRightIcon data-icon="inline-end" /></Button>
      </>}
      {!c.call_id && !c.external && c.doc_id && <Button size="sm" variant="outline" onClick={() => setDocumentOpen(true)}>Open document<ArrowUpRightIcon data-icon="inline-end" /></Button>}
      {c.external && c.url && /^https?:\/\//i.test(c.url) && <Button size="sm" variant="outline" nativeButton={false} render={<a href={c.url} target="_blank" rel="noreferrer" />}>Open original<ArrowUpRightIcon data-icon="inline-end" /></Button>}
    </div>
    {audioChanged && <p className="mt-3 text-xs text-amber-800">The current recording differs from the audio cited in this answer. Playback at this timestamp is unavailable.</p>}
    {call.isLoading && <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground"><Spinner />Loading transcript context</p>}
    {(call.error || doc.error) && <p role="status" className="mt-4 text-xs text-destructive">The original source isn’t available in this pod. The saved excerpt remains above.</p>}
    {!!nearby.length && <details className="mt-6 border-t pt-4">
      <summary className="cursor-pointer text-xs font-medium">Surrounding transcript context</summary>
      {changed && <p className="mt-2 text-xs text-amber-800">The current transcript differs from the saved passage above.</p>}
      <p className="mt-3 text-sm leading-7 text-muted-foreground">{nearby.map(sentence => <span key={sentence.id}>{sentence.id === c.sentence_id ? <mark className="bg-blue-100 text-foreground">{sentence.text}</mark> : sentence.text}{" "}</span>)}</p>
    </details>}
    <details className="mt-6 border-t pt-4 text-xs">
      <summary className="cursor-pointer font-medium">Provenance details</summary>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-muted-foreground">
        <dt>Source</dt><dd className="break-all">{c.call_id || c.doc_id || c.url || "External source"}</dd>
        {c.sentence_id && <><dt>Sentence</dt><dd>{c.sentence_id}</dd></>}
        {c.asr_model && <><dt>Transcription</dt><dd className="break-all">{c.asr_model}</dd></>}
        {c.attribution_method && <><dt>Speaker labels</dt><dd>{c.attribution_method === "reference_timing" || c.attribution_method.startsWith("transferred_from_reference_timing") ? "Mapped from reference timing" : c.attribution_method.replaceAll("_", " ")}</dd></>}
        {c.transcript_sha256 && <><dt>Transcript SHA-256</dt><dd className="break-all font-mono">{c.transcript_sha256}</dd></>}
        {c.audio_sha256 && <><dt>Audio SHA-256</dt><dd className="break-all font-mono">{c.audio_sha256}</dd></>}
      </dl>
    </details>
    <DocumentSheet docId={documentOpen ? c.doc_id : null} onClose={() => setDocumentOpen(false)} />
  </div>;
}

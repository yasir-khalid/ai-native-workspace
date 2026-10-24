"use client";

import { useMemo, useState } from "react";
import { ChevronDownIcon, QuoteIcon } from "lucide-react";
import { useCallPlayer } from "@/components/calls/calls-player";
import { Badge, Card, Mono, type Tone } from "@/components/common";
import { Button } from "@/components/ui/button";
import { asrModelLabel, formatCallDate, formatClock, type CallDetail } from "@/lib/calls";
import { cn } from "@/lib/utils";

type Block = { kind: "heading" | "bullet" | "text"; text: string };

function decodeEntities(value: string): string {
  return value
    .replace(/&nbsp;/g, " ")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function summaryBlocks(html: string): Block[] {
  const text = html
    .replace(/\r/g, "")
    .replace(/<h3[^>]*>/gi, "\n## ")
    .replace(/<\/h3>/gi, "\n")
    .replace(/<li[^>]*>/gi, "\n• ")
    .replace(/<\/li>/gi, "")
    .replace(/<\/(ul|ol|p|div)>/gi, "\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "");
  return decodeEntities(text)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      if (line.startsWith("## ")) return { kind: "heading" as const, text: line.slice(3) };
      if (line.startsWith("• ")) return { kind: "bullet" as const, text: line.slice(2) };
      return { kind: "text" as const, text: line };
    });
}

function werTone(wer: number | undefined): Tone {
  if (wer === undefined) return "gray";
  if (wer <= 0.08) return "green";
  if (wer <= 0.12) return "amber";
  return "red";
}

export function InsightsPanel({ call }: { call: CallDetail }) {
  const [expanded, setExpanded] = useState(false);
  const { playCall } = useCallPlayer();
  const blocks = useMemo(() => summaryBlocks(call.summary_long_html), [call.summary_long_html]);
  const sentences = useMemo(() => {
    const map = new Map<string, { text: string; speaker: string; start: number }>();
    for (const paragraph of call.transcript.paragraphs) {
      for (const sentence of paragraph.sentences) {
        map.set(sentence.id, { text: sentence.text, speaker: paragraph.speaker, start: sentence.start });
      }
    }
    return map;
  }, [call.transcript.paragraphs]);
  const confidence = Math.round(call.asr.avg_confidence * 1000) / 10;

  return (
    <div className="grid gap-4">
      <Card className="p-5 md:p-6">
        <h2 className="text-base font-semibold tracking-[-0.01em]">Editorial summary</h2>
        <p className="mt-2 text-[14px] leading-6 text-gray-900">{call.summary_short}</p>
        {blocks.length > 0 && (
          <>
            <div className={cn("mt-4 border-t pt-4", !expanded && "hidden")}>
              <div className="grid gap-2.5">
                {blocks.map((block, index) => {
                  if (block.kind === "heading") {
                    return <h3 key={index} className="mt-2 text-[13px] font-semibold first:mt-0">{block.text}</h3>;
                  }
                  if (block.kind === "bullet") {
                    return (
                      <div key={index} className="flex gap-2 text-[13px] leading-6 text-gray-900">
                        <span className="mt-[0.6em] size-1 shrink-0 rounded-full bg-muted-foreground" />
                        <p>{block.text}</p>
                      </div>
                    );
                  }
                  return <p key={index} className="text-[13px] leading-6 text-gray-900">{block.text}</p>;
                })}
              </div>
            </div>
            <Button variant="ghost" size="sm" className="mt-3 h-7 px-2 text-[11px]" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
              <ChevronDownIcon className={cn("size-3.5 transition-transform duration-200 motion-reduce:transition-none", expanded && "rotate-180")} />
              {expanded ? "Show less" : "Read full summary"}
            </Button>
          </>
        )}
        <div className="mt-4 flex flex-wrap gap-1.5 border-t pt-4">
          {call.tags.map((tag) => <Badge key={tag} tone="gray">{tag}</Badge>)}
        </div>
      </Card>

      {call.insights.length > 0 && (
        <Card className="p-5 md:p-6">
          <div className="flex items-center gap-2">
            <QuoteIcon className="size-4 text-muted-foreground" />
            <h2 className="text-base font-semibold tracking-[-0.01em]">Evidence</h2>
          </div>
          <div className="mt-4 grid gap-5">
            {call.insights.map((insight) => (
              <section key={insight.title}>
                <h3 className="text-[13px] font-semibold">{insight.title}</h3>
                <div className="mt-1.5 grid gap-1">
                  {insight.sentence_ids.map((id) => {
                    const sentence = sentences.get(id);
                    if (!sentence) return null;
                    return (
                      <button
                        key={id}
                        type="button"
                        onClick={() => playCall(call, Math.max(0, sentence.start - 0.2))}
                        className="group grid grid-cols-[auto_minmax(0,1fr)] gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                        aria-label={`Play evidence at ${formatClock(sentence.start)}`}
                      >
                        <span className="font-mono text-[10px] text-muted-foreground tabular-nums">{formatClock(sentence.start)}</span>
                        <span className="text-[12px] leading-5 text-gray-900">
                          <span className="font-medium text-foreground">{sentence.speaker}</span>
                          <span className="text-muted-foreground"> · </span>
                          “{sentence.text}”
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        </Card>
      )}

      <Card className="p-5 md:p-6">
        <h2 className="text-base font-semibold tracking-[-0.01em]">Transcript</h2>
        <div className="mt-3 grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-[12px]">
          <span className="text-muted-foreground">Model</span>
          <span className="flex flex-wrap gap-1.5">
            <Badge tone="purple">{call.asr.engine}</Badge>
            <Badge tone="blue">{asrModelLabel(call.asr.model)}</Badge>
          </span>
          <span className="text-muted-foreground">Language</span>
          <span><Badge tone="gray">{call.asr.language.toUpperCase()}</Badge></span>
          <span className="text-muted-foreground">Words</span>
          <span className="tabular-nums">{call.asr.word_count.toLocaleString()} · {call.asr.sentence_count.toLocaleString()} sentences</span>
          <span className="text-muted-foreground">Confidence</span>
          <span><Badge tone={confidence >= 95 ? "green" : "amber"}>{confidence}% average</Badge></span>
          <span className="text-muted-foreground">WER vs reference</span>
          <span>
            <Badge tone={werTone(call.asr.wer_vs_reference)}>
              {call.asr.wer_vs_reference === undefined ? "—" : `${(call.asr.wer_vs_reference * 100).toFixed(1)}%`}
            </Badge>
          </span>
          <span className="text-muted-foreground">Audio SHA</span>
          <span title={call.audio_sha256}><Mono>{call.audio_sha256.slice(0, 12)}</Mono></span>
          <span className="text-muted-foreground">Source</span>
          <span className="text-gray-900">{call.event_title} · {formatCallDate(call.event_date, true)}</span>
        </div>
        <p className="mt-4 border-t pt-3 text-[11px] leading-5 text-muted-foreground">
          Generated with a local model; timestamps and quotes come from the audio.
        </p>
      </Card>
    </div>
  );
}

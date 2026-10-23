"use client";

import { AudioLinesIcon } from "lucide-react";
import { useCallPlayer } from "@/components/calls/calls-player";
import { Card } from "@/components/common";
import { formatClock, type CallDetail } from "@/lib/calls";
import { cn } from "@/lib/utils";

export function ChaptersPanel({ call }: { call: CallDetail }) {
  const { current, playing, time, playCall } = useCallPlayer();
  const isCurrent = current?.call_id === call.call_id;
  const activeIndex = isCurrent
    ? call.chapters.findIndex((chapter) => time >= chapter.start && time < chapter.end)
    : -1;

  return (
    <Card className="overflow-hidden">
      <ol className="divide-y">
        {call.chapters.map((chapter, index) => {
          const active = index === activeIndex;
          const progress = active ? Math.min(100, Math.max(0, ((time - chapter.start) / Math.max(chapter.end - chapter.start, 1)) * 100)) : 0;
          return (
            <li key={chapter.id} className="relative">
              <button
                type="button"
                onClick={() => playCall(call, chapter.start)}
                className={cn(
                  "grid w-full grid-cols-[2rem_minmax(0,1fr)_3.5rem] items-start gap-3 px-3 py-4 text-left transition-colors hover:bg-muted/60 focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring focus-visible:outline-none md:px-5",
                  active && "bg-accent/60",
                )}
                aria-label={`Play chapter ${index + 1}: ${chapter.title}`}
              >
                <span className={cn("pt-0.5 font-mono text-[11px] text-muted-foreground tabular-nums", active && "text-foreground")}>
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-[14px] leading-5 font-semibold">{chapter.title}</span>
                    {active && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-primary px-1.5 py-0.5 text-[9px] font-medium text-primary-foreground">
                        <AudioLinesIcon className="size-2.5" />
                        {playing ? "Playing" : "Current"}
                      </span>
                    )}
                  </span>
                  <span className="mt-1 block text-[12px] leading-5 text-muted-foreground">{chapter.summary}</span>
                  {active && (
                    <span className="mt-2 block h-0.5 overflow-hidden rounded-full bg-muted">
                      <span className="block h-full rounded-full bg-foreground" style={{ width: `${progress}%` }} />
                    </span>
                  )}
                </span>
                <span className="pt-0.5 text-right font-mono text-[10px] text-muted-foreground tabular-nums">{formatClock(chapter.start)}</span>
              </button>
              {active && <span className="absolute inset-y-0 left-0 w-0.5 bg-ring" />}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

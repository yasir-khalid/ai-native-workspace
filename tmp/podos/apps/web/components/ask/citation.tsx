"use client";

import { useAuiState } from "@assistant-ui/react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { shortDate } from "@/lib/api";
import { formatClock } from "@/lib/calls";
import type { Citation } from "@/lib/types";
import { useEvidence } from "./evidence";

type Part = { type: string; toolName?: string; text?: string; result?: unknown };

/** Reference numbers resolve only against the sources of this individual answer. */
export function CitationChip({ refNum }: { refNum: number }) {
  const open = useEvidence();
  const parts = useAuiState(s => s.message.content) as unknown as Part[];
  const sources = (parts.find(p => p.type === "tool-call" && (p.toolName === "podos.sources" || p.toolName === "podos.registry"))?.result ?? []) as Citation[];
  const cite = sources.find(c => c.ref === refNum);
  return <Tooltip>
    <TooltipTrigger render={<Button type="button" variant="ghost" aria-disabled={!cite}
      onClick={() => {
        if (!cite) return;
        const text = parts.filter(part => part.type === "text").map(part => part.text ?? "").join("");
        const refs = new Set([...text.matchAll(/\[(\d+)\]/g)].map(match => Number(match[1])));
        open(cite, sources.filter(source => refs.has(source.ref)));
      }}
      className="mx-0.5 inline-flex h-[18px] min-w-[18px] -translate-y-px items-center justify-center rounded-full bg-blue-200 px-1.5 align-middle text-[11px] leading-none font-medium text-blue-900 hover:bg-blue-300 aria-disabled:bg-muted aria-disabled:text-muted-foreground"
      aria-label={cite ? `Source ${refNum}: ${cite.speaker || cite.title}${cite.start !== undefined ? ` at ${formatClock(cite.start)}` : ""}` : `Source ${refNum} unavailable`}>
      {refNum}
    </Button>} />
    <TooltipContent className="max-w-xs">
      {cite ? <>
        <div className="font-medium">{cite.title}</div>
        <div className="opacity-70">{cite.external ? "External source" : cite.source} · {shortDate(cite.published_at)}</div>
        {cite.speaker && <div>{cite.speaker}{cite.role ? ` · ${cite.role}` : ""}{cite.start !== undefined ? ` · ${formatClock(cite.start)}` : ""}</div>}
        {cite.text && <p className="mt-1 line-clamp-5">{cite.text}</p>}
        <p className="mt-1 text-xs opacity-70">Inspect evidence</p>
      </> : "This reference has no matching source in the answer."}
    </TooltipContent>
  </Tooltip>;
}

"use client";

import { useState } from "react";
import { ArrowDownIcon, MessageSquareIcon, PlayIcon } from "lucide-react";
import { useCallPlayer } from "@/components/calls/calls-player";
import { Card } from "@/components/common";
import { Button } from "@/components/ui/button";
import { formatClock, type CallDetail, type QA } from "@/lib/calls";
import { cn } from "@/lib/utils";

function QACard({ call, item }: { call: CallDetail; item: QA }) {
  const [expanded, setExpanded] = useState(false);
  const { playCall } = useCallPlayer();
  const long = item.answer.length > 240;
  return (
    <Card className="min-w-0 p-4 md:p-5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
        <MessageSquareIcon className="size-3.5" />
        <span className="font-medium text-foreground">{item.speaker}</span>
        <button
          type="button"
          onClick={() => playCall(call, item.start)}
          className="rounded px-1 font-mono text-[10px] tabular-nums transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          aria-label={`Play question at ${formatClock(item.start)}`}
        >
          {formatClock(item.start)}
        </button>
      </div>
      <p className="mt-2 text-[15px] leading-6 font-medium text-balance">{item.question}</p>
      <div className="mt-3 border-l-2 border-border pl-3">
        <p className="text-[11px] text-muted-foreground">{item.answer_speaker}</p>
        <p className={cn("mt-1 text-[13px] leading-6 text-gray-900", long && !expanded && "line-clamp-3")}>{item.answer}</p>
        {long && (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            className="mt-1 rounded text-[11px] font-medium text-link hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {expanded ? "Show less" : "Read full answer"}
          </button>
        )}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="outline" size="sm" className="h-7 rounded-md text-[11px]" onClick={() => playCall(call, item.answer_start)}>
          <ArrowDownIcon /> Jump to answer
        </Button>
        <Button variant="ghost" size="sm" className="h-7 rounded-md text-[11px]" onClick={() => playCall(call, item.start)}>
          <PlayIcon /> Play from question
        </Button>
      </div>
    </Card>
  );
}

export function QAPanel({ call }: { call: CallDetail }) {
  const { playCall } = useCallPlayer();
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4">
      {call.key_questions.length > 0 && (
        <div>
          <h2 className="mb-2 text-[12px] font-semibold text-muted-foreground">Key questions</h2>
          <div className="flex flex-wrap gap-1.5">
            {call.key_questions.map((question) => (
              <button
                key={question.sentence_id}
                type="button"
                onClick={() => playCall(call, question.start)}
                className="min-w-0 max-w-full rounded-full border bg-card px-2.5 py-1 text-left text-[11px] text-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                title={question.question}
                aria-label={`Play key question at ${formatClock(question.start)}: ${question.question}`}
              >
                <span className="block max-w-[34rem] truncate">{question.question}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {call.qa.length === 0 ? (
        <Card className="p-8 text-center text-sm text-muted-foreground">No analyst questions were detected in this call.</Card>
      ) : call.qa.map((item) => <QACard key={item.id} call={call} item={item} />)}
    </div>
  );
}

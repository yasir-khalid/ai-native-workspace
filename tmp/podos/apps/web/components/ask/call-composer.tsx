"use client";

import { createContext, useContext, useRef, useState } from "react";
import Link from "next/link";
import { ComposerPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import { AtSignIcon, AudioLinesIcon, CheckIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { ComposerAction } from "@/components/assistant-ui/elements/thread.aui";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { callKind, formatCallDate, type CallSummary } from "@/lib/calls";

export const CallComposerContext = createContext<{ callId: string | null; setCallId: (id: string | null) => void }>({ callId: null, setCallId: () => {} });

function label(call: CallSummary) { return `${call.ticker} · ${callKind(call.event_title)} · ${formatCallDate(call.event_date)}`; }

export function CallComposer({ autoFocus }: { autoFocus: boolean }) {
  const { pod } = usePod();
  const { callId, setCallId } = useContext(CallComposerContext);
  const { data: calls, error } = usePodData<CallSummary[]>(`/v1/pods/${pod}/calls`);
  const aui = useAui();
  const input = useRef<HTMLTextAreaElement>(null);
  const running = useAuiState(state => state.thread.isRunning);
  const [query, setQuery] = useState<{ from: number; to: number; text: string } | null>(null);
  const [index, setIndex] = useState(0);
  const selected = calls?.find(call => call.call_id === callId);
  const matches = (calls ?? []).filter(call => `${call.company} ${call.ticker} ${call.event_title} ${call.event_date}`.toLowerCase().includes(query?.text.toLowerCase().trim() ?? ""));

  function detect(target: HTMLTextAreaElement) {
    const to = target.selectionStart;
    const match = /(?:^|\s)@([^@\n]{0,80})$/.exec(target.value.slice(0, to));
    setQuery(match ? { from: to - match[1].length - 1, to, text: match[1] } : null);
    setIndex(0);
  }
  function choose(call: CallSummary) {
    const text = aui.composer.getState().text;
    if (query) aui.composer.setText(text.slice(0, query.from) + text.slice(query.to));
    setCallId(call.call_id);
    setQuery(null);
    requestAnimationFrame(() => input.current?.focus());
  }
  return <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
    <ComposerPrimitive.AttachmentDropzone render={<div data-slot="aui_composer-shell" className="flex w-full flex-col gap-2 rounded-3xl bg-muted p-2 focus-within:ring-1 focus-within:ring-ring" />}>
      {callId && <div className="flex min-w-0 items-center gap-1 px-2 pt-1" aria-label="Attached call transcript">
        <Badge variant="secondary" className="min-w-0 gap-1.5"><AudioLinesIcon data-icon="inline-start" /><span className="truncate">@{selected ? label(selected) : "Call transcript"}</span></Badge>
        <Button type="button" variant="ghost" size="icon-xs" aria-label="Remove call context" onClick={() => setCallId(null)}><XIcon /></Button>
      </div>}
      <ComposerPrimitive.Input ref={input} placeholder="Ask a question, or type @ to reference a call…" rows={1} autoFocus={autoFocus}
        aria-label="Message input" aria-expanded={!!query} aria-controls={query ? "call-mention-list" : undefined}
        aria-activedescendant={query && matches[index] ? `mention-${matches[index].call_id}` : undefined}
        className="aui-composer-input max-h-48 min-h-10 w-full resize-none bg-transparent px-3.5 pt-2 text-base leading-6 outline-none"
        onChange={event => detect(event.target)} onSelect={event => detect(event.currentTarget)}
        onKeyDown={event => {
          if (!query || event.nativeEvent.isComposing) return;
          if (event.key === "Escape") { event.preventDefault(); setQuery(null); }
          else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); setIndex(i => Math.max(0, Math.min(matches.length - 1, i + (event.key === "ArrowDown" ? 1 : -1))));
          } else if ((event.key === "Enter" || event.key === "Tab") && matches[index]) { event.preventDefault(); choose(matches[index]); }
        }} />
      <div className="flex items-center gap-1 px-1">
        <Popover open={!!query} onOpenChange={open => { if (!open) setQuery(null); }}>
          <PopoverTrigger render={<Button type="button" variant="ghost" size="sm" disabled={running} aria-label="Add call reference"
            onClick={() => { const position = input.current?.selectionStart ?? aui.composer.getState().text.length; setQuery({ from: position, to: position, text: "" }); setIndex(0); }} />}>
            <AtSignIcon data-icon="inline-start" />Reference
          </PopoverTrigger>
          <PopoverContent side="top" align="start" initialFocus={false} finalFocus={false} className="w-[min(25rem,calc(100vw-3rem))] p-1">
            <div className="px-3 py-2 text-xs font-medium text-muted-foreground">Calls in this pod</div>
            <div id="call-mention-list" role="listbox" aria-label="Call references" className="max-h-64 overflow-y-auto">
              {!calls && !error && <div className="flex items-center gap-2 p-3 text-sm"><Spinner />Loading calls</div>}
              {error && <p className="p-3 text-sm text-destructive">Couldn&apos;t load calls. Try again shortly.</p>}
              {calls && !matches.length && <p className="p-3 text-sm text-muted-foreground">No matching calls</p>}
              {matches.map((call, i) => <Button key={call.call_id} id={`mention-${call.call_id}`} type="button" variant="ghost" role="option"
                aria-selected={i === index} onMouseDown={event => event.preventDefault()} onClick={() => choose(call)}
                className="h-auto w-full justify-start gap-2 rounded-md p-3 data-[selected=true]:bg-accent" data-selected={i === index}>
                <AudioLinesIcon data-icon="inline-start" /><span className="grid min-w-0 gap-0.5 text-left"><span className="truncate">{call.company}</span><span className="truncate text-xs text-muted-foreground">{label(call)}</span></span>
                {call.call_id === callId && <CheckIcon data-icon="inline-end" />}
              </Button>)}
            </div>
          </PopoverContent>
        </Popover>
        <ComposerAction />
      </div>
    </ComposerPrimitive.AttachmentDropzone>
  </ComposerPrimitive.Root>;
}

export function UserCallReference() {
  const { pod } = usePod();
  const callId = useAuiState(state => state.message.metadata.custom?.callId) as string | null;
  const { data } = usePodData<CallSummary>(callId ? `/v1/pods/${pod}/calls/${encodeURIComponent(callId)}` : null);
  if (!callId) return null;
  return <Link href={`/${pod}/calls/${encodeURIComponent(callId)}`} className="justify-self-end" aria-label="Open referenced call">
    <Badge variant="secondary" className="max-w-full gap-1.5"><AudioLinesIcon data-icon="inline-start" /><span className="truncate">@{data ? label(data) : "Call transcript"}</span></Badge>
  </Link>;
}

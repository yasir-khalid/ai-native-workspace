"use client";

import Link from "next/link";
import { usePod } from "@/components/shell/session";
import { Cover } from "@/components/calls/cover";
import { useCallPlayer } from "@/components/calls/calls-player";
import { Button } from "@/components/ui/button";
import { useSidebar } from "@/components/ui/sidebar";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { audioSrc, formatClock } from "@/lib/calls";
import { cn } from "@/lib/utils";
import { CheckIcon, DownloadIcon, PauseIcon, PlayIcon, RotateCcwIcon, RotateCwIcon, Volume2Icon, XIcon } from "lucide-react";
import type { CSSProperties } from "react";

const SPEEDS = [0.75, 1, 1.25, 1.5, 2];

function Scrubber({ compact = false }: { compact?: boolean }) {
  const { time, duration, current, buffered, seek } = useCallPlayer();
  const max = Math.max(duration || current?.duration || 0, 1);
  const played = Math.min(100, (time / max) * 100);
  const loaded = Math.min(100, (buffered / max) * 100);
  return (
    <div className={cn("group relative w-full", compact ? "h-1" : "h-1.5")}>
      <div className="absolute inset-0 overflow-hidden rounded-full bg-muted">
        <div className="absolute inset-y-0 left-0 bg-muted-foreground/25 transition-[width] duration-300" style={{ width: `${loaded}%` }} />
        <div className="absolute inset-y-0 left-0 bg-foreground" style={{ width: `${played}%` }} />
      </div>
      {!compact && (
        <span
          className="pointer-events-none absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-foreground opacity-0 transition-opacity group-hover:opacity-100 group-has-[:focus-visible]:opacity-100"
          style={{ left: `${played}%` }}
        />
      )}
      <input
        type="range"
        min={0}
        max={max}
        step={0.1}
        value={Math.min(time, max)}
        onChange={(event) => seek(Number(event.target.value))}
        aria-label="Seek through call"
        aria-valuetext={`${formatClock(time)} of ${formatClock(max)}`}
        className="absolute inset-0 h-full w-full cursor-pointer appearance-none rounded-full bg-transparent opacity-0 focus-visible:opacity-100 focus-visible:outline-none"
      />
    </div>
  );
}

function PlayButton({ size = "md" }: { size?: "sm" | "md" }) {
  const { playing, toggle, current } = useCallPlayer();
  return (
    <Button
      type="button"
      onClick={toggle}
      aria-label={playing ? "Pause call" : "Play call"}
      className={cn("rounded-full bg-primary text-primary-foreground hover:bg-primary/90", size === "md" ? "size-9" : "size-8")}
    >
      {playing ? <PauseIcon className={size === "md" ? "size-4" : "size-3.5"} fill="currentColor" /> : <PlayIcon className={cn(size === "md" ? "size-4" : "size-3.5", "translate-x-px")} fill="currentColor" />}
      <span className="sr-only">{current?.company}</span>
    </Button>
  );
}

export function PlayerBar() {
  const { pod } = usePod();
  const { state } = useSidebar();
  const { current, playing, time, duration, rate, setRate, volume, setVolume, skip, close, chapterTitle } = useCallPlayer();
  if (!current) return null;
  const href = `/${pod}/calls/${current.call_id}`;
  const src = audioSrc(current.audio_url);
  const style = {
    "--player-left": state === "collapsed" ? "var(--sidebar-width-icon)" : "var(--sidebar-width)",
  } as CSSProperties;
  return (
    <div
      role="region"
      aria-label="Call player"
      style={style}
      className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] transition-[left] duration-200 ease-linear md:left-(--player-left)"
    >
      <div className="absolute inset-x-0 top-0 md:hidden">
        <Scrubber compact />
      </div>
      <div className="flex h-[68px] items-center gap-3 px-4 md:hidden">
        <Link href={href} className="shrink-0 rounded-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" aria-label={`Open ${current.company} call`}>
          <Cover ticker={current.ticker} callId={current.call_id} size="sm" playing={playing} />
        </Link>
        <div className="min-w-0 flex-1">
          <Link href={href} className="block truncate text-[13px] font-semibold hover:underline">
            {current.company}
          </Link>
          <span className="block truncate text-[11px] text-muted-foreground">{chapterTitle ?? `${current.ticker} · ${current.event_title}`}</span>
        </div>
        <PlayButton size="sm" />
        <Button variant="ghost" size="icon-sm" onClick={close} aria-label="Close player" className="text-muted-foreground hover:text-foreground">
          <XIcon className="size-4" />
        </Button>
      </div>
      <div className="mx-auto hidden h-20 w-full max-w-[1600px] grid-cols-[minmax(0,1fr)_minmax(280px,2fr)_minmax(0,1fr)] items-center gap-6 px-4 md:grid">
        <div className="flex min-w-0 items-center gap-3">
          <Link href={href} className="shrink-0 rounded-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" aria-label={`Open ${current.company} call`}>
            <Cover ticker={current.ticker} callId={current.call_id} size="sm" playing={playing} />
          </Link>
          <div className="min-w-0">
            <Link href={href} className="flex min-w-0 items-baseline gap-2 hover:underline">
              <span className="truncate text-[13px] font-semibold">{current.company}</span>
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{current.ticker}</span>
            </Link>
            <span className="block truncate text-[11px] text-muted-foreground">{chapterTitle ?? current.event_title}</span>
          </div>
        </div>
        <div className="flex min-w-0 flex-col items-center gap-1">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="icon-sm" onClick={() => skip(-15)} aria-label="Back 15 seconds" className="text-muted-foreground hover:text-foreground">
              <RotateCcwIcon className="size-4" />
            </Button>
            <PlayButton />
            <Button variant="ghost" size="icon-sm" onClick={() => skip(15)} aria-label="Forward 15 seconds" className="text-muted-foreground hover:text-foreground">
              <RotateCwIcon className="size-4" />
            </Button>
          </div>
          <div className="flex w-full items-center gap-2">
            <span className="w-10 shrink-0 text-right font-mono text-[10px] text-muted-foreground tabular-nums">{formatClock(time)}</span>
            <Scrubber />
            <span className="w-10 shrink-0 font-mono text-[10px] text-muted-foreground tabular-nums">{formatClock(duration)}</span>
          </div>
        </div>
        <div className="flex min-w-0 items-center justify-end gap-1.5">
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="sm" className="h-7 px-2 font-mono text-[11px] text-muted-foreground hover:text-foreground" aria-label={`Playback speed ${rate} times`}>
                  {rate}×
                </Button>
              }
            />
            <DropdownMenuContent side="top" align="center" className="w-28">
              {SPEEDS.map((speed) => (
                <DropdownMenuItem key={speed} onClick={() => setRate(speed)} className="justify-between font-mono text-xs">
                  {speed}× {rate === speed && <CheckIcon className="size-3.5" />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <div className="hidden items-center gap-1.5 lg:flex">
            <Volume2Icon className="size-3.5 text-muted-foreground" />
            <input
              type="range"
              min={0}
              max={100}
              value={Math.round(volume * 100)}
              onChange={(event) => setVolume(Number(event.target.value) / 100)}
              aria-label="Volume"
              className="h-1 w-20 cursor-pointer accent-[var(--foreground)]"
            />
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            nativeButton={false}
            render={<a href={src} download aria-label="Download call audio" />}
            className="text-muted-foreground hover:text-foreground"
          >
            <DownloadIcon className="size-4" />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={close} aria-label="Close player" className="text-muted-foreground hover:text-foreground">
            <XIcon className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

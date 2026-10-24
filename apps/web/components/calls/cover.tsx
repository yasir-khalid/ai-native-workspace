import type { CSSProperties } from "react";
import { cn } from "@/lib/utils";

const PALETTES = [
  ["#6d5bd0", "#2f7d72"],
  ["#345995", "#7a5c8e"],
  ["#9a5a3f", "#516b62"],
  ["#3f6b54", "#8b6b3f"],
  ["#5f5478", "#3f7089"],
  ["#78526d", "#4e6c8a"],
];

const SIZES = {
  sm: "size-9 rounded-md",
  md: "size-12 rounded-lg",
  lg: "size-24 rounded-xl",
  xl: "size-48 rounded-2xl md:size-64",
  detail: "size-40 rounded-2xl md:size-64",
  episode: "size-20 rounded-xl sm:size-36 md:size-40 xl:size-44",
};

const TICKER = {
  sm: "text-[10px] px-1",
  md: "text-xs px-1.5",
  lg: "text-2xl px-3",
  xl: "text-5xl px-5 md:text-6xl",
  detail: "text-4xl px-4 md:text-6xl",
  episode: "px-2 text-[20px] sm:px-3 sm:text-[36px] xl:text-[44px]",
};

const LABEL = {
  sm: "text-[6px]",
  md: "text-[7px]",
  lg: "text-[9px]",
  xl: "text-[10px] md:text-[11px]",
  detail: "text-[10px] md:text-[11px]",
  episode: "text-[7px] sm:text-[10px]",
};

function hash(value: string): number {
  let h = 2166136261;
  for (let i = 0; i < value.length; i++) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

export function callPalette(seed: string): [string, string] {
  return PALETTES[hash(seed) % PALETTES.length] as [string, string];
}

export function Cover({
  ticker,
  callId,
  label,
  size = "md",
  playing = false,
  className,
}: {
  ticker: string;
  callId: string;
  label?: string;
  size?: keyof typeof SIZES;
  playing?: boolean;
  className?: string;
}) {
  const seed = `${ticker}-${callId}`;
  const [a, b] = callPalette(seed);
  const h = hash(seed);
  const bars = [4 + (h % 4), 8 + (h % 6), 5 + ((h >> 3) % 5), 9 + ((h >> 6) % 5)];
  const points = bars.map((height, index) => `${index * 7 + 2},${12 - height}`).join(" ");
  const style = {
    "--cover-a": a,
    "--cover-b": b,
    background:
      "radial-gradient(120% 130% at 8% 0%, color-mix(in oklab, var(--cover-a) 78%, transparent), transparent 58%), radial-gradient(110% 120% at 96% 100%, color-mix(in oklab, var(--cover-b) 72%, transparent), transparent 55%), var(--primary)",
    color: "var(--primary-foreground)",
  } as CSSProperties;
  return (
    <div
      aria-hidden
      style={style}
      className={cn("relative isolate flex shrink-0 items-center justify-center overflow-hidden border border-primary-foreground/10 select-none", SIZES[size], className)}
    >
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.13]"
        style={{
          backgroundImage:
            "repeating-linear-gradient(90deg, currentColor 0, currentColor 1px, transparent 1px, transparent 16px), repeating-linear-gradient(0deg, currentColor 0, currentColor 1px, transparent 1px, transparent 16px)",
        }}
      />
      {label && (size === "lg" || size === "xl" || size === "detail" || size === "episode") && (
        <span className={cn("absolute top-2 left-2.5 font-mono font-medium tracking-[0.13em] uppercase opacity-65", LABEL[size])}>
          {label}
        </span>
      )}
      <span className={cn("relative text-center font-bold tracking-[-0.06em]", TICKER[size])}>{ticker.slice(0, 5)}</span>
      <svg
        viewBox="0 0 25 12"
        className={cn("absolute right-2.5 bottom-2 h-2.5 opacity-35", size === "sm" && "hidden")}
        fill="none"
      >
        <polyline points={points} stroke="currentColor" strokeWidth="0.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span
        className={cn(
          "absolute right-2.5 bottom-2 flex h-2.5 items-end gap-[2px] transition-opacity duration-200",
          !playing && "opacity-0",
          size === "sm" && "hidden",
        )}
      >
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="w-[2px] rounded-full bg-current motion-safe:animate-pulse"
            style={{ height: `${[6, 10, 7][index]}px`, animationDelay: `${index * 130}ms`, animationDuration: "900ms" }}
          />
        ))}
      </span>
    </div>
  );
}

import type { ReactNode } from "react";
import { BotIcon, ChartLineIcon, FileSearchIcon, LandmarkIcon, LeafIcon, RadarIcon, SparklesIcon } from "lucide-react";
import { Badge as UIBadge } from "@/components/ui/badge";
import { Card as UICard } from "@/components/ui/card";
import { Empty as UIEmpty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

/** Linear-style primitives: tinted labels (hue @ 100 background, hue @ 900 text), solid status dots,
 * and hairline-bordered tiles. The 900 step clears 4.5:1 on the matching 100 tint; the saturated 700
 * steps fail for text. Status meaning is always also in the label text, never colour alone. Labels and
 * tiles share one tint scale, so the icon and any pill for the same subject read as the same colour. */
export type Tone = "gray" | "blue" | "green" | "amber" | "red" | "purple" | "teal" | "pink";

const TILE: Record<Tone, string> = {
  gray: "bg-gray-100 text-gray-900 border-gray-200",
  blue: "bg-blue-100 text-blue-900 border-blue-200",
  green: "bg-green-100 text-green-900 border-green-200",
  amber: "bg-amber-100 text-amber-900 border-amber-200",
  red: "bg-red-100 text-red-900 border-red-200",
  purple: "bg-purple-100 text-purple-700 border-purple-200",
  teal: "bg-teal-100 text-teal-900 border-teal-200",
  pink: "bg-pink-100 text-pink-900 border-pink-200",
};

/* Status dots are non-text graphics, so each tone must clear 3:1 against the page and cards. */
const DOT: Record<Tone, string> = {
  gray: "bg-gray-600",
  blue: "bg-blue-700",
  green: "bg-green-800",
  amber: "bg-amber-900",
  red: "bg-red-700",
  purple: "bg-purple-700",
  teal: "bg-teal-700",
  pink: "bg-pink-700",
};

export function Badge({ tone = "gray", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <UIBadge variant={tone} className={cn("px-1.5", className)}>
      {children}
    </UIBadge>
  );
}

/** Back-compat alias used across pages. */
export const Tag = Badge;

export function Dot({ tone, pulse }: { tone: Tone; pulse?: boolean }) {
  return <span className={cn("size-2 shrink-0 rounded-full", DOT[tone], pulse && "animate-pulse motion-reduce:animate-none")} />;
}

export function Tile({ tone = "gray", children, size = "md" }: { tone?: Tone; children: ReactNode; size?: "sm" | "md" }) {
  return (
    <span className={cn("grid shrink-0 place-items-center rounded-md border", TILE[tone], size === "md" ? "size-9 [&_svg]:size-4" : "size-7 [&_svg]:size-3.5")}>
      {children}
    </span>
  );
}

/* Agents get a stable colour + icon so they're recognisable everywhere. */
const AGENT: Record<string, { tone: Tone; icon: typeof BotIcon; name: string }> = {
  "auto-router": { tone: "gray", icon: SparklesIcon, name: "Auto" },
  "equity-research": { tone: "blue", icon: ChartLineIcon, name: "Equity Research" },
  "filing-analyst": { tone: "purple", icon: FileSearchIcon, name: "Filing Analyst" },
  "catalyst-monitor": { tone: "amber", icon: RadarIcon, name: "Catalyst Monitor" },
  "esg-screener": { tone: "green", icon: LeafIcon, name: "ESG Screener" },
  "entity-intelligence": { tone: "teal", icon: LandmarkIcon, name: "Institutional Entity Intelligence" },
};

/* User-facing names for platform identifiers, so ids, versions and team slugs never reach the UI. */
const titleCase = (s: string) => s.replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export const agentName = (id: string | null | undefined) => (id ? AGENT[id]?.name ?? titleCase(id) : "PodOS");

const AGENT_ABOUT: Record<string, string> = {
  "equity-research": "Answers questions about companies, theses and catalysts from your pod's research, with sources.",
  "filing-analyst": "Reads 10-Q, 10-K and 8-K filings and pulls out what changed: risks, guidance and notable edits.",
  "catalyst-monitor": "Watches your pod's tickers for new filings and catalysts, and asks for sign-off before sending a brief.",
  "esg-screener": "Screens companies for ESG risks and controversies.",
  "entity-intelligence": "Answers questions about legal entities from public records: identity, hierarchy and reported holdings.",
};
export const agentDescription = (id: string, fallback: string) => AGENT_ABOUT[id] ?? fallback;

/** "alice.chen" → "Alice Chen" (the dev directory uses first.last subjects). */
export const personName = (id: string | null | undefined) => (id ? titleCase(id) : "");

const CAPABILITY: Record<string, [name: string, description: string]> = {
  "research.search": ["Search your pod's research", "Find notes, filings, call notes and models in your pod's research."],
  "document.export.pptx": ["Create PowerPoint decks", "Turn an answer into a slide deck that only your pod can see."],
  "document.export.docx": ["Create Word memos", "Turn an answer into a memo that only your pod can see."],
  "research.monitor.create": ["Set up monitors", "Watch tickers and topics and alert you when something changes."],
};
export const capabilityName = (id: string) => CAPABILITY[id]?.[0] ?? titleCase(id);
export const capabilityDescription = (id: string, fallback: string) => CAPABILITY[id]?.[1] ?? fallback;

const CLASSIFICATION: Record<string, string> = {
  pod_confidential: "Private to this pod",
  firm_internal: "Firm internal",
  public: "Public",
};
export const classificationName = (c: string) => CLASSIFICATION[c.toLowerCase()] ?? titleCase(c.toLowerCase());

export function agentTone(agentId: string): Tone {
  return AGENT[agentId]?.tone ?? "teal";
}

export function AgentTile({ agentId, size = "md" }: { agentId: string; size?: "sm" | "md" }) {
  const a = AGENT[agentId] ?? { tone: "teal" as Tone, icon: BotIcon };
  const Icon = a.icon;
  return (
    <Tile tone={a.tone} size={size}>
      <Icon />
    </Tile>
  );
}

/* Each pod gets a stable solid colour from the palette, with its initial. */
const POD_ACCENT: Record<string, string> = { indigo: "bg-purple-700", emerald: "bg-green-800", admin: "bg-gray-1000" };

export function PodAvatar({ pod, size = "md" }: { pod: { display_name: string; accent: string }; size?: "sm" | "md" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid shrink-0 place-items-center rounded-md font-bold text-white",
        POD_ACCENT[pod.accent] ?? "bg-blue-700",
        size === "md" ? "size-6 text-[11px]" : "size-5 text-[10px]",
      )}
    >
      {pod.display_name[0]}
    </span>
  );
}

/* ------------------------------------------------------------------------------------------------ */

export function Page({ children, header, wide }: { children: ReactNode; header?: ReactNode; wide?: boolean }) {
  return (
    <div className="relative min-h-svh overflow-hidden bg-canvas">
      {header}
      <div className={cn("relative z-10 mx-auto w-full px-5 py-8 md:px-10 md:py-10", wide ? "max-w-[1200px]" : "max-w-[1200px]")}>{children}</div>
    </div>
  );
}

export function PageHeader({ title, description, actions, wide, eyebrow, actionsClassName }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; wide?: boolean; eyebrow?: string; actionsClassName?: string }) {
  return (
    <div className="border-b bg-background">
      <div className={cn("mx-auto flex w-full flex-wrap items-end justify-between gap-6 px-5 pt-14 pb-7 md:px-10 md:pt-10", wide ? "max-w-[1200px]" : "max-w-[1200px]")}>
        <div className="min-w-0">
          {eyebrow && <p className="field-label mb-2">{eyebrow}</p>}
          <h1 className="text-[28px] leading-[1.15] font-bold tracking-[-0.025em] md:text-[32px]">{title}</h1>
          {description && <p className="mt-2 max-w-2xl text-[15px] text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className={cn("flex shrink-0 items-center gap-2", actionsClassName)}>{actions}</div>}
      </div>
    </div>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <UICard className={cn("gap-0 rounded-lg py-0 shadow-none", className)}>{children}</UICard>;
}

const STATE: Record<string, { label: string; tone: Tone; pulse?: boolean }> = {
  COMPLETED: { label: "Completed", tone: "green" },
  RUNNING: { label: "Running", tone: "amber", pulse: true },
  QUEUED: { label: "Queued", tone: "gray" },
  WAITING_FOR_HUMAN: { label: "Needs approval", tone: "blue" },
  APPROVED: { label: "Approved", tone: "blue" },
  FAILED: { label: "Failed", tone: "red" },
  REJECTED: { label: "Rejected", tone: "red" },
  CANCELLED: { label: "Cancelled", tone: "gray" },
  EXPIRED: { label: "Expired", tone: "gray" },
  OPEN: { label: "Open", tone: "gray" },
  CHANGES_REQUESTED: { label: "Changes requested", tone: "amber" },
};

export function StateBadge({ state, className }: { state: string; className?: string }) {
  const s = STATE[state] ?? { label: state.toLowerCase(), tone: "gray" as Tone };
  return (
    <span className={cn("inline-flex items-center gap-2 text-[13px]", className)}>
      <Dot tone={s.tone} pulse={s.pulse} />
      {s.label}
    </span>
  );
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <Card className="text-center">
      <UIEmpty className="px-6 py-14">
        <EmptyHeader>
          {icon && <EmptyMedia variant="icon">{icon}</EmptyMedia>}
          <EmptyTitle>{title}</EmptyTitle>
          {children && <EmptyDescription>{children}</EmptyDescription>}
        </EmptyHeader>
      </UIEmpty>
    </Card>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-xs text-muted-foreground", className)}>{children}</span>;
}

export function Segmented<T extends string>({
  value, onChange, options,
}: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[] }) {
  return (
    <Tabs className="gap-0" value={value} onValueChange={(next) => next && onChange(next as T)}>
      <TabsList className="h-8 rounded-md border bg-muted p-0.5">
        {options.map((o) => (
          <TabsTrigger
            key={o.value}
            value={o.value}
            className="h-7 flex-none rounded-[5px] px-3 text-[13px] font-medium text-muted-foreground after:hidden data-active:bg-card data-active:text-foreground data-active:shadow-[0_0_0_1px_var(--border)]"
          >
            {o.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}

export function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="mt-10">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-base font-semibold tracking-[-0.01em]">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

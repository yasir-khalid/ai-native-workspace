"use client";

import { useState } from "react";
import { createLibrary, defineComponent, Renderer } from "@openuidev/react-lang";
import { z } from "zod/v4";
import { ArrowUpRightIcon, CalendarDaysIcon, ChevronDownIcon, Clock3Icon, MapPinIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { courtResultSchema, type CourtResult } from "@/lib/ui-results";

const money = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" });
function checkedAt(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/London",
  }).format(date);
}

function Slots({ slots, priceFrom }: { slots: CourtResult["venues"][number]["slots"]; priceFrom?: number | null }) {
  const row = (slot: typeof slots[number], index: number) => (
    <div key={`${slot.start}-${slot.end}-${index}`} className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="flex min-w-0 items-center gap-3">
        <Clock3Icon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
        <div><div className="text-sm font-medium tabular-nums">{slot.start} – {slot.end}</div>
          <div className="text-xs text-muted-foreground">{slot.price_gbp !== null ? money.format(slot.price_gbp) : priceFrom != null ? `From ${money.format(priceFrom)}` : "Price on provider site"}</div>
        </div>
      </div>
      {slot.booking_url && <Button variant="outline" size="sm" nativeButton={false}
        render={<a href={slot.booking_url} target="_blank" rel="noopener noreferrer" />}>
        View booking<ArrowUpRightIcon data-icon="inline-end" />
      </Button>}
    </div>
  );
  return <div className="divide-y">
    {slots.slice(0, 3).map(row)}
    {slots.length > 3 && <Collapsible>
      <CollapsibleTrigger className="flex w-full items-center justify-between gap-2 py-3 text-xs font-medium">
        Show {slots.length - 3} more time{slots.length - 3 === 1 ? "" : "s"}<ChevronDownIcon className="size-3.5" />
      </CollapsibleTrigger>
      <CollapsibleContent className="divide-y">{slots.slice(3).map(row)}</CollapsibleContent>
    </Collapsible>}
  </div>;
}

function CourtResultsView({ result }: { result: CourtResult }) {
  const [all, setAll] = useState(false);
  return <section className="my-4 flex min-w-0 flex-col gap-3" aria-label="Court availability results" data-testid="court-results">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="text-xs font-medium text-muted-foreground">{result.source} · {result.venues.length} venue{result.venues.length === 1 ? "" : "s"}</div>
      <Badge variant="outline">Availability snapshot</Badge>
    </div>
    {!result.venues.length && <Empty><EmptyHeader><EmptyTitle>No matching availability</EmptyTitle>
      <EmptyDescription>Try another time, date or search radius.</EmptyDescription></EmptyHeader></Empty>}
    <div className="grid min-w-0 gap-3">
      {(all ? result.venues : result.venues.slice(0, 5)).map((venue, index) => <Card key={`${venue.name}-${venue.date}-${index}`}>
        <CardHeader>
          <CardTitle>{venue.name}</CardTitle>
          <CardDescription>{venue.operator || "Court provider"}</CardDescription>
          <CardAction><Badge variant={venue.stale ? "secondary" : "outline"}>{venue.stale ? "May be stale" : "Available"}</Badge></CardAction>
        </CardHeader>
        <CardContent>
          <div className="mb-2 flex flex-col gap-2 text-xs text-muted-foreground">
            {venue.address && <p className="flex items-start gap-2"><MapPinIcon className="mt-0.5 size-3.5 shrink-0" />{venue.address}
              {venue.distance_miles !== null && <span className="shrink-0 tabular-nums">· {venue.distance_miles.toLocaleString("en-GB", { maximumFractionDigits: 1 })} mi</span>}</p>}
            <p className="flex items-center gap-2"><CalendarDaysIcon className="size-3.5 shrink-0" />{venue.date || "Date from search"} · London time</p>
          </div>
          <Slots slots={venue.slots} priceFrom={venue.price_from_gbp} />
        </CardContent>
        <CardFooter><p className="text-xs leading-relaxed text-muted-foreground">
          {checkedAt(venue.last_refreshed) && <>Updated {checkedAt(venue.last_refreshed)} · </>}
          {venue.stale ? "Availability may have changed. " : ""}Confirm times and prices with the provider. Booking opens on their site.
        </p></CardFooter>
      </Card>)}
    </div>
    {result.venues.length > 5 && <Button variant="ghost" size="sm" onClick={() => setAll(value => !value)}>{all ? "Show fewer venues" : `Show all ${result.venues.length} venues`}</Button>}
    {result.truncated && <p className="text-xs text-muted-foreground">Showing the first 20 venues returned by the provider.</p>}
  </section>;
}

// Controlled OpenUI: real tool data fills an allowlisted component, without a second LLM pass.
// Research answers retain the existing declarative OpenUI chart/table library.
const CourtResults = defineComponent({
  name: "CourtResults", description: "Court availability from validated provider data, with explicit booking links.",
  // OpenUI's JSON Schema validator prunes required nullable fields. Keep the
  // payload opaque to that parser and validate it here, preserving unknown prices.
  props: z.object({ result: z.unknown() }),
  component: ({ props }) => {
    const parsed = courtResultSchema.safeParse(props.result);
    return parsed.success ? <CourtResultsView result={parsed.data} /> : null;
  },
});
const resultLibrary = createLibrary({ root: "CourtResults", components: [CourtResults] });

export function ResultCards({ result }: { result: unknown }) {
  const parsed = courtResultSchema.safeParse(result);
  if (!parsed.success) return null;
  return <Renderer response={`root = CourtResults(${JSON.stringify(parsed.data)})`} library={resultLibrary} isStreaming={false} />;
}

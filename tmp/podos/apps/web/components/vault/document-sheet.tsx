"use client";

import { Badge, type Tone } from "@/components/common";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { shortDate } from "@/lib/api";
import type { ResearchDocument } from "@/lib/types";

const KIND: Record<string, { label: string; tone: Tone }> = {
  note: { label: "Note", tone: "gray" },
  filing: { label: "Filing", tone: "blue" },
  transcript: { label: "Call notes", tone: "purple" },
  model: { label: "Model", tone: "teal" },
  news: { label: "News", tone: "amber" },
};

export function KindTag({ kind }: { kind: string }) {
  const k = KIND[kind] ?? { label: kind, tone: "gray" as Tone };
  return <Badge tone={k.tone}>{k.label}</Badge>;
}

/** Highlights the pod's isolation canary markers so leakage would be obvious in a demo. */
function Body({ text }: { text: string }) {
  const parts = text.split(/(\[[A-Z]+-PRIVATE-[0-9a-f]+\])/g);
  return (
    <p className="text-[15px] leading-7 text-foreground/90">
      {parts.map((p, i) =>
        /^\[[A-Z]+-PRIVATE-/.test(p) ? (
          <span key={i} className="rounded bg-amber-200 px-1 font-mono text-xs text-amber-900">{p}</span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </p>
  );
}

export function DocumentSheet({ docId, onClose }: { docId: string | null; onClose: () => void }) {
  const { pod } = usePod();
  const { data } = usePodData<ResearchDocument>(docId ? `/v1/pods/${pod}/documents/${docId}` : null);
  return (
    <Sheet open={!!docId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-xl">
        {!data ? (
          <div className="space-y-3 p-6">
            <Skeleton className="h-5 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : (
          <>
            <SheetHeader className="gap-1.5 border-b px-6 py-5">
              <div className="flex items-center gap-2">
                <KindTag kind={data.kind} />
                {data.ticker && <span className="text-xs font-medium text-muted-foreground">{data.ticker}</span>}
              </div>
              <SheetTitle className="text-lg font-semibold">{data.title}</SheetTitle>
              <SheetDescription>
                {data.source} · {shortDate(data.published_at)}
              </SheetDescription>
            </SheetHeader>
            <div className="overflow-y-auto px-6 py-5">
              <Body text={data.body ?? ""} />
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

"use client";

import { ArrowDownToLineIcon, FileTextIcon, PresentationIcon } from "lucide-react";
import { toast } from "sonner";
import { Badge, Card, Empty, personName, Tile } from "@/components/common";
import { Button } from "@/components/ui/button";
import { usePod } from "@/components/shell/session";
import { downloadArtifact, explain, relTime } from "@/lib/api";
import type { ArtifactRef } from "@/lib/types";

export function ArtifactsTable({ items }: { items: ArtifactRef[] }) {
  const { pod } = usePod();
  if (!items.length) return <Empty title="No files yet">Ask an agent for a deck or memo.</Empty>;
  return (
    <Card className="divide-y">
      {items.map((a) => {
        const prov = (a.provenance ?? {}) as Record<string, unknown>;
        const via = prov.via_agent ? "Agent" : prov.via_workload ? "Approved brief" : "Export";
        const viaTone = prov.via_agent ? "blue" : prov.via_workload ? "green" : "gray";
        const pptx = a.filename.endsWith(".pptx");
        return (
          <div key={a.artifact_id} className="flex items-center gap-3 px-4 py-3">
            <Tile tone={pptx ? "amber" : "blue"} size="sm">{pptx ? <PresentationIcon /> : <FileTextIcon />}</Tile>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{a.title || a.filename}</div>
              <div className="truncate text-xs text-muted-foreground">
                {a.filename} · {personName(a.created_by)} · {relTime(a.created_at)}
              </div>
              <Badge tone={viaTone} className="mt-1.5 sm:hidden">{via}</Badge>
            </div>
            <Badge tone={viaTone} className="hidden sm:inline-flex">{via}</Badge>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"
              onClick={() => downloadArtifact(pod, a.artifact_id).catch((e) => toast.error(explain(e)))}
              aria-label={`Download ${a.filename}`}
            >
              <ArrowDownToLineIcon className="size-4" />
            </Button>
          </div>
        );
      })}
    </Card>
  );
}

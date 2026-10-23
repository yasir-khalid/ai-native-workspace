"use client";

import { prepareAsk } from "@/lib/ask-handoff";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MessageSquareIcon } from "lucide-react";
import { toast } from "sonner";
import { Badge, Card, classificationName, Empty, Page, personName, Section } from "@/components/common";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { ArtifactsTable } from "@/components/vault/artifacts-table";
import { DocumentSheet, KindTag } from "@/components/vault/document-sheet";
import {
  Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api, explain, shortDate } from "@/lib/api";
import type { ArtifactRef, ResearchCase, ResearchDocument } from "@/lib/types";

type CaseDetail = ResearchCase & { documents: ResearchDocument[]; artifacts: ArtifactRef[] };

function CaseView() {
  const { caseId } = useParams<{ caseId: string }>();
  const { pod } = usePod();
  const router = useRouter();
  const { data, mutate, error } = usePodData<CaseDetail>(`/v1/pods/${pod}/research-cases/${caseId}`);
  const [open, setOpen] = useState<string | null>(null);
  const [note, setNote] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [exporting, setExporting] = useState(false);

  if (error) return <Page><p className="text-sm text-muted-foreground">This case isn&apos;t available in this pod.</p></Page>;
  if (!data) return <Page><Skeleton className="h-6 w-80" /><Skeleton className="mt-6 h-64 rounded-lg" /></Page>;

  async function exportDeck() {
    setExporting(true);
    try {
      await api(`/v1/pods/${pod}/exports`, {
        body: {
          capability_id: "document.export.pptx",
          idempotency_key: `case-${caseId}-${Date.now()}`,
          research_case_id: caseId,
          spec: {
            title: `${data!.tickers.join(", ")}: case summary`,
            subtitle: data!.title,
            slides: [
              { title: "Thesis", bullets: [data!.thesis] },
              { title: "Evidence on file", bullets: data!.documents.slice(0, 8).map((d) => `${d.title} (${shortDate(d.published_at)})`) },
            ],
          },
        },
      });
      toast.success("Deck added to this case");
      await mutate();
    } catch (e) {
      toast.error(explain(e));
    } finally {
      setExporting(false);
    }
  }

  return (
    <Page
      header={
        <div className="border-b bg-background">
          <div className="mx-auto w-full max-w-[1200px] px-5 pt-14 pb-8 md:px-10 md:pt-10">
            <Breadcrumb className="mb-5">
              <BreadcrumbList className="text-[13px]">
                <BreadcrumbItem>
                  <BreadcrumbLink render={<Link href={`/${pod}/vault`} />}>Research</BreadcrumbLink>
                </BreadcrumbItem>
                <BreadcrumbSeparator />
                <BreadcrumbItem className="min-w-0">
                  <BreadcrumbPage className="truncate">{data.title}</BreadcrumbPage>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 max-w-3xl">
                <h1 className="text-[28px] leading-9 font-semibold tracking-[-0.04em] md:text-[32px] md:leading-10">{data.title}</h1>
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  {data.tickers.map((t) => <Badge key={t} tone="gray">{t}</Badge>)}
                  <Badge tone="purple">{classificationName(data.classification)}</Badge>
                  <span className="ml-1 text-xs text-muted-foreground">Owner {personName(data.owner_user_id)}</span>
                </div>
                <p className="mt-4 text-[15px] leading-7 text-gray-900">{data.thesis}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" className="h-8 rounded-md" onClick={() => setNote(true)}>Add note</Button>
                <Button size="sm" variant="outline" className="h-8 rounded-md" disabled={exporting} onClick={exportDeck}>Export deck</Button>
                <Button size="sm" className="h-8 rounded-md" onClick={() => { router.push(prepareAsk(pod, { caseId })); }}>
                  <MessageSquareIcon /> Ask about this case
                </Button>
              </div>
            </div>
          </div>
        </div>
      }
    >
      <Section title={`Documents · ${data.documents.length}`}>
        {data.documents.length === 0 ? (
          <Empty title="No documents yet" />
        ) : (
          <Card className="divide-y">
            {data.documents.map((d) => (
              <Button key={d.doc_id} variant="ghost" onClick={() => setOpen(d.doc_id)} className="h-auto w-full flex-col items-start gap-0 whitespace-normal rounded-none px-4 py-3.5 text-left hover:bg-gray-100">
                <div className="flex w-full min-w-0 items-center gap-2">
                  <KindTag kind={d.kind} />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{d.title}</span>
                  <span className="ml-auto shrink-0 text-xs text-muted-foreground">{shortDate(d.published_at)}</span>
                </div>
                <p className="mt-1 w-full line-clamp-2 text-sm text-muted-foreground">{d.excerpt}</p>
              </Button>
            ))}
          </Card>
        )}
      </Section>
      <Section title={`Files · ${data.artifacts.length}`}>
        <ArtifactsTable items={data.artifacts} />
      </Section>

      <DocumentSheet docId={open} onClose={() => setOpen(null)} />
      <Dialog open={note} onOpenChange={setNote}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add note</DialogTitle>
            <DialogDescription>Saved to this case and searchable by this pod&apos;s agents.</DialogDescription>
          </DialogHeader>
          <FieldGroup className="gap-4">
            <Field><FieldLabel htmlFor="n-title">Title</FieldLabel><Input id="n-title" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
            <Field><FieldLabel htmlFor="n-body">Note</FieldLabel><Textarea id="n-body" rows={6} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={() => setNote(false)}>Cancel</Button>
            <Button disabled={title.length < 3 || !body} onClick={async () => {
              try {
                await api(`/v1/pods/${pod}/documents`, { body: { title, body, case_id: caseId, ticker: data.tickers[0] } });
                setNote(false); setTitle(""); setBody(""); await mutate(); toast.success("Note saved");
              } catch (e) { toast.error(explain(e)); }
            }}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Page>
  );
}

export default function CasePage() {
  return <Suspense fallback={null}><CaseView /></Suspense>;
}

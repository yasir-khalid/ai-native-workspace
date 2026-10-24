"use client";

import Link from "next/link";
import { useState } from "react";
import { FolderIcon, PlusIcon, SearchIcon, XIcon } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { Badge, Card, Page, PageHeader, personName, Segmented, Tile } from "@/components/common";
import { usePod } from "@/components/shell/session";
import { usePodData } from "@/components/shell/hooks";
import { ArtifactsTable } from "@/components/vault/artifacts-table";
import { DocumentSheet, KindTag } from "@/components/vault/document-sheet";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Empty as UIEmpty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Textarea } from "@/components/ui/textarea";
import { api, explain, relTime, shortDate } from "@/lib/api";
import type { ArtifactRef, ResearchCase, ResearchDocument } from "@/lib/types";

type Hit = ResearchDocument & { snippet: string; score: number };

function NewCase({ open, setOpen }: { open: boolean; setOpen: (o: boolean) => void }) {
  const { pod } = usePod();
  const { mutate } = useSWRConfig();
  const [title, setTitle] = useState("");
  const [thesis, setThesis] = useState("");
  const [tickers, setTickers] = useState("");
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New research case</DialogTitle>
          <DialogDescription>Visible only to members of this pod.</DialogDescription>
        </DialogHeader>
        <FieldGroup className="gap-4">
          <Field><FieldLabel htmlFor="c-title">Name</FieldLabel><Input id="c-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Northwind: 1.6T ramp" /></Field>
          <Field><FieldLabel htmlFor="c-tick">Tickers</FieldLabel><Input id="c-tick" value={tickers} onChange={(e) => setTickers(e.target.value)} placeholder="NWSC, LGS" /></Field>
          <Field><FieldLabel htmlFor="c-thesis">Thesis</FieldLabel><Textarea id="c-thesis" rows={4} value={thesis} onChange={(e) => setThesis(e.target.value)} /></Field>
        </FieldGroup>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            disabled={title.length < 3}
            onClick={async () => {
              try {
                await api(`/v1/pods/${pod}/research-cases`, { body: { title, thesis, tickers: tickers.split(",").map((t) => t.trim()).filter(Boolean) } });
                await mutate([`/v1/pods/${pod}/research-cases`, pod]);
                setOpen(false);
                toast.success("Case created");
              } catch (e) {
                toast.error(explain(e));
              }
            }}
          >
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function VaultPage() {
  const { pod, info } = usePod();
  const cases = usePodData<ResearchCase[]>(`/v1/pods/${pod}/research-cases`);
  const docs = usePodData<ResearchDocument[]>(`/v1/pods/${pod}/documents`);
  const arts = usePodData<ArtifactRef[]>(`/v1/pods/${pod}/artifacts`);
  const [tab, setTab] = useState<"cases" | "documents" | "files">("cases");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [newCase, setNewCase] = useState(false);

  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    try {
      setHits(await api<Hit[]>(`/v1/pods/${pod}/search?q=${encodeURIComponent(q)}`, { pod }));
    } catch (err) {
      toast.error(explain(err));
    }
  }

  return (
    <Page
      header={
        <PageHeader
          title="Research"
          description={`Cases, documents and generated files owned by ${info.display_name}.`}
          actions={<Button size="sm" className="h-8 rounded-md" onClick={() => setNewCase(true)}><PlusIcon /> New case</Button>}
        />
      }
    >
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <form onSubmit={search} className="min-w-72 flex-1">
          <InputGroup className="h-9 bg-background has-[[data-slot=input-group-control]:focus-visible]:ring-blue-300">
            <InputGroupAddon><SearchIcon className="size-4 text-gray-800" /></InputGroupAddon>
            <InputGroupInput value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search notes, filings and transcripts…" aria-label="Search research" className="placeholder:text-gray-800" />
            {hits && (
              <InputGroupAddon align="inline-end">
                <InputGroupButton type="button" size="icon-xs" aria-label="Clear search" onClick={() => { setHits(null); setQ(""); }} className="text-gray-800 hover:text-foreground">
                  <XIcon className="size-4" />
                </InputGroupButton>
              </InputGroupAddon>
            )}
          </InputGroup>
        </form>
        {!hits && (
          <Segmented
            value={tab}
            onChange={setTab}
            options={[
              { value: "cases", label: `Cases ${cases.data?.length ?? ""}` },
              { value: "documents", label: `Documents ${docs.data?.length ?? ""}` },
              { value: "files", label: `Files ${arts.data?.length ?? ""}` },
            ]}
          />
        )}
      </div>

      {hits ? (
        <Card className="divide-y">
          {hits.map((h) => (
            <Button key={h.doc_id} variant="ghost" onClick={() => setOpen(h.doc_id)} className="h-auto w-full flex-col items-start gap-0 whitespace-normal rounded-none px-4 py-3.5 text-left hover:bg-gray-100">
              <div className="flex w-full min-w-0 items-center gap-2"><KindTag kind={h.kind} /><span className="min-w-0 flex-1 truncate text-sm font-medium">{h.title}</span><span className="ml-auto shrink-0 text-xs text-muted-foreground">{shortDate(h.published_at)}</span></div>
              <p className="mt-1 w-full line-clamp-2 text-sm text-muted-foreground">{h.snippet}</p>
            </Button>
          ))}
          {hits.length === 0 && (
            <UIEmpty className="py-10">
              <EmptyHeader>
                <EmptyTitle>No results in this pod</EmptyTitle>
                <EmptyDescription>Try a different search term.</EmptyDescription>
              </EmptyHeader>
            </UIEmpty>
          )}
        </Card>
      ) : (
        <>
          {tab === "cases" && (
            <div className="grid gap-4 md:grid-cols-2">
              {(cases.data ?? []).map((c) => (
                <Link key={c.case_id} href={`/${pod}/vault/${c.case_id}`} className="block min-w-0 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
                  <Card className="h-full p-5 transition-[border-color,box-shadow] hover:border-gray-300 hover:shadow-sm">
                    <div className="flex items-center gap-3">
                      <Tile tone="teal"><FolderIcon /></Tile>
                      <div className="min-w-0 flex-1">
                        <div className="line-clamp-2 text-[15px] leading-5 font-semibold">{c.title}</div>
                        <div className="text-xs text-muted-foreground">Updated {relTime(c.updated_at)} · {personName(c.owner_user_id)}</div>
                      </div>
                    </div>
                    <p className="mt-4 line-clamp-2 text-sm text-gray-900">{c.thesis}</p>
                    <div className="mt-4 flex items-center gap-1.5">
                      {c.tickers.map((t) => <Badge key={t} tone="gray">{t}</Badge>)}
                      <span className="ml-auto text-xs text-muted-foreground">{c.documents} documents · {c.artifacts} files</span>
                    </div>
                  </Card>
                </Link>
              ))}
            </div>
          )}
          {tab === "documents" && (
            <Card className="divide-y">
              {(docs.data ?? []).map((d) => (
                <Button key={d.doc_id} variant="ghost" onClick={() => setOpen(d.doc_id)} className="h-auto w-full justify-start gap-3 rounded-none px-4 py-3 text-left hover:bg-gray-100">
                  <span className="w-20"><KindTag kind={d.kind} /></span>
                  <span className="min-w-0 flex-1 truncate text-sm">{d.title}</span>
                  {d.ticker && <span className="font-mono text-xs text-muted-foreground">{d.ticker}</span>}
                  <span className="hidden truncate text-xs text-muted-foreground md:block md:w-56">{d.case_title}</span>
                  <span className="w-20 text-right text-xs text-muted-foreground">{shortDate(d.published_at)}</span>
                </Button>
              ))}
            </Card>
          )}
          {tab === "files" && <ArtifactsTable items={arts.data ?? []} />}
        </>
      )}
      <DocumentSheet docId={open} onClose={() => setOpen(null)} />
      <NewCase open={newCase} setOpen={setNewCase} />
    </Page>
  );
}

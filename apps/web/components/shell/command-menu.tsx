"use client";

import { prepareAsk } from "@/lib/ask-handoff";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  BriefcaseBusinessIcon, FolderIcon, InboxIcon, LibraryIcon, LogOutIcon, MessageSquareIcon, RadarIcon,
  SquarePenIcon, SunIcon,
} from "lucide-react";
import {
  Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator, CommandShortcut,
} from "@/components/ui/command";
import { PodAvatar } from "@/components/common";
import { AgentMenuDetails } from "@/components/agent-menu-details";
import { canChatWithAgent, orderedAgents } from "@/lib/agent-availability";
import type { CatalogueAgent, ResearchCase, ThreadSummary } from "@/lib/types";
import { signOut, usePod } from "./session";
import { usePodData } from "./hooks";

/* ⌘K command menu: one place to jump to any page, chat, research case, agent or pod. */

const PAGES = [
  { href: "ask", label: "New chat", icon: SquarePenIcon, keys: "N" },
  { href: "today", label: "Today", icon: SunIcon, keys: "G T" },
  { href: "inbox", label: "Inbox", icon: InboxIcon, keys: "G I" },
  { href: "vault", label: "Research", icon: LibraryIcon, keys: "G R" },
  { href: "inbox?tab=monitors", label: "Monitors", icon: RadarIcon, keys: "G M" },
  { href: "inbox?tab=activity", label: "Activity", icon: BriefcaseBusinessIcon, keys: "G W" },
];
const GO: Record<string, string> = { t: "today", i: "inbox", r: "vault", m: "inbox?tab=monitors", w: "inbox?tab=activity" };

const CommandMenuContext = createContext<{ open: () => void }>({ open: () => {} });
export const useCommandMenu = () => useContext(CommandMenuContext);

function typingInField(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  return !!t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
}

export function CommandMenuProvider({ children }: { children: ReactNode }) {
  const { pod } = usePod();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  // ⌘K / Ctrl+K toggles the menu; "/" opens it; N starts a chat; G then a letter jumps to a page (Linear-style).
  useEffect(() => {
    let pendingG = 0;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || typingInField(e) || document.querySelector("[role=dialog]")) return;
      const k = e.key.toLowerCase();
      if (k === "/") {
        e.preventDefault();
        setOpen(true);
      } else if (k === "n") {
        router.push(`/${pod}/ask`);
      } else if (k === "g") {
        pendingG = Date.now();
      } else if (GO[k] && Date.now() - pendingG < 1200) {
        pendingG = 0;
        router.push(`/${pod}/${GO[k]}`);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pod, router]);

  const ctx = useMemo(() => ({ open: () => setOpen(true) }), []);
  return (
    <CommandMenuContext.Provider value={ctx}>
      {children}
      <CommandDialog open={open} onOpenChange={setOpen} title="Search PodOS" description="Jump to a page, chat, research case or agent">
        {open && <Menu close={() => setOpen(false)} />}
      </CommandDialog>
    </CommandMenuContext.Provider>
  );
}

function Menu({ close }: { close: () => void }) {
  const { pod, pods, info } = usePod();
  const router = useRouter();
  const threads = usePodData<ThreadSummary[]>(`/v1/pods/${pod}/threads`);
  const cases = usePodData<ResearchCase[]>(`/v1/pods/${pod}/research-cases`);
  const agents = usePodData<CatalogueAgent[]>(`/v1/pods/${pod}/agents`, { refreshInterval: 15_000 });
  const go = useCallback((href: string) => { close(); router.push(href); }, [close, router]);
  const chatWith = (agentId: string) => {
    go(prepareAsk(pod, agentId === "auto-router" ? {} : { agentId }));
  };

  return (
    <Command>
      <CommandInput placeholder="Search pages, chats, research, agents…" />
      <CommandList className="max-h-[min(420px,60vh)]">
        <CommandEmpty>No results.</CommandEmpty>
        <CommandGroup heading="Go to">
          {PAGES.map((p) => (
            <CommandItem key={p.href} value={`page ${p.label}`} onSelect={() => go(`/${pod}/${p.href}`)}>
              <p.icon /> {p.label}
              <CommandShortcut>{p.keys}</CommandShortcut>
            </CommandItem>
          ))}
        </CommandGroup>
        {!!threads.data?.length && (
          <CommandGroup heading="Chats">
            {threads.data.slice(0, 8).map((t) => (
              <CommandItem key={t.thread_id} value={`chat ${t.title ?? "New chat"} ${t.thread_id}`} onSelect={() => go(`/${pod}/ask/${t.thread_id}`)}>
                <MessageSquareIcon /> <span className="truncate">{t.title ?? "New chat"}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        {!!cases.data?.length && (
          <CommandGroup heading="Research cases">
            {cases.data.map((c) => (
              <CommandItem key={c.case_id} value={`case ${c.title} ${c.tickers.join(" ")}`} onSelect={() => go(`/${pod}/vault/${c.case_id}`)}>
                <FolderIcon /> <span className="truncate">{c.title}</span>
                <CommandShortcut className="tracking-normal">{c.tickers.join(", ")}</CommandShortcut>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        {!!agents.data?.length && (
          <CommandGroup heading="Your agents · live health">
            {orderedAgents(agents.data).map((a) => (
              <CommandItem key={a.agent_id} value={`agent ${a.name}`} keywords={[a.agent_id, a.kind]} disabled={!canChatWithAgent(a)} onSelect={() => chatWith(a.agent_id)} className="items-start gap-3 py-2.5 data-[disabled=true]:opacity-100 [&>svg:last-child]:hidden">
                <AgentMenuDetails agent={a} />
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        <CommandSeparator />
        {pods.length > 1 && (
          <CommandGroup heading="Switch pod">
            {pods.filter((p) => p.pod_id !== info.pod_id).map((p) => (
              <CommandItem key={p.pod_id} value={`pod ${p.display_name}`} onSelect={() => go(`/${p.pod_id}/ask`)}>
                <PodAvatar pod={p} size="sm" /> {p.display_name}
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        <CommandGroup heading="Account">
          <CommandItem value="sign out" onSelect={() => { close(); void signOut(); }}>
            <LogOutIcon /> Sign out
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </Command>
  );
}

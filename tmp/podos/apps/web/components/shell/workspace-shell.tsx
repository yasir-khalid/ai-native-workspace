"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { SWRConfig } from "swr";
import {
  AudioLinesIcon, CheckIcon, ChevronDownIcon, InboxIcon, LibraryIcon, LogOutIcon,
  PauseIcon, PlayIcon, SearchIcon, SquarePenIcon, SunIcon,
} from "lucide-react";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu,
  SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarRail, SidebarTrigger, useSidebar,
} from "@/components/ui/sidebar";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { DossierMark } from "@/components/dossier-mark";
import { PodAvatar } from "@/components/common";
import { CallsPlayerProvider, useCallPlayer } from "@/components/calls/calls-player";
import { Cover } from "@/components/calls/cover";
import { PlayerBar } from "@/components/calls/player-bar";
import { cn } from "@/lib/utils";
import type { ThreadSummary } from "@/lib/types";
import { CommandMenuProvider, useCommandMenu } from "./command-menu";
import { PodContextReact, signOut, useMe, usePod } from "./session";
import { usePodData } from "./hooks";
import { PodosRunsProvider, useRunningChats } from "@/components/ask/podos-runtime";
import { Spinner } from "@/components/ui/spinner";

const NAV = [
  { href: "today", label: "Today", icon: SunIcon },
  { href: "vault", label: "Research", icon: LibraryIcon },
  { href: "calls", label: "Calls", icon: AudioLinesIcon },
  { href: "inbox", label: "Inbox", icon: InboxIcon },
];

function PodSwitcher() {
  const { info, pods } = usePod();
  const router = useRouter();
  const pathname = usePathname();
  const section = pathname.split("/")[2] ?? "ask";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" className="h-8 w-full justify-start gap-2 px-2 hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-ring group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
            <PodAvatar pod={info} />
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground group-data-[collapsible=icon]:hidden">{info.display_name}</span>
            <ChevronDownIcon className="size-3.5 text-muted-foreground group-data-[collapsible=icon]:hidden" />
          </Button>
        }
        aria-label="Switch pod"
      />
      <DropdownMenuContent className="w-64" align="start">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Your pods</DropdownMenuLabel>
          {pods.map((p) => (
            <DropdownMenuItem
              key={p.pod_id}
              onClick={() => router.push(`/${p.pod_id}/${["ask", ...NAV.map((n) => n.href)].includes(section) ? section : "ask"}`)}
            >
              <PodAvatar pod={p} size="sm" />
              <div className="grid min-w-0 leading-tight">
                <span className="truncate">{p.display_name}</span>
                <span className="truncate text-xs text-muted-foreground">{p.strategy}</span>
              </div>
              {p.pod_id === info.pod_id && <CheckIcon className="ml-auto size-4" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function recency(iso: string) {
  const days = Math.floor((new Date().setHours(0, 0, 0, 0) - new Date(iso).setHours(0, 0, 0, 0)) / 86_400_000);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "Previous 7 days" : days < 30 ? "Previous 30 days" : "Older";
}

function Chats() {
  const { pod } = usePod();
  const pathname = usePathname();
  const runningThreads = useRunningChats();
  const { data } = usePodData<ThreadSummary[]>(`/v1/pods/${pod}/threads`, { refreshInterval: 3000 });
  if (!data?.length) return null;
  const groups = new Map<string, ThreadSummary[]>();
  for (const t of data.slice(0, 40)) {
    const g = recency(t.updated_at ?? t.created_at);
    groups.set(g, [...(groups.get(g) ?? []), t]);
  }
  return (
    <Collapsible defaultOpen className="group/chats group-data-[collapsible=icon]:hidden">
      <SidebarGroup className="py-0">
        <SidebarGroupLabel
          render={<CollapsibleTrigger />}
          className="h-7 w-full cursor-pointer px-2 text-xs font-normal text-muted-foreground hover:text-foreground"
        >
          Chats
          <ChevronDownIcon className="ml-auto size-3.5 transition-transform group-data-[closed]/chats:-rotate-90" />
        </SidebarGroupLabel>
        <CollapsibleContent>
          {[...groups].map(([label, items]) => (
            <div key={label} className="mb-2">
              <div className="px-2 pt-1.5 pb-1 text-[11px] font-medium text-subtle">{label}</div>
              <SidebarMenu className="gap-0">
                {items.map((t) => {
                  const href = `/${pod}/ask/${t.thread_id}`;
                  return (
                    <SidebarMenuItem key={t.thread_id}>
                      <SidebarMenuButton
                        isActive={pathname === href}
                        render={<Link href={href} />}
                        className="h-7 rounded-md px-2 text-[13px] font-normal text-gray-900 data-active:bg-sidebar-accent data-active:text-gray-1000"
                      >
                        <span className="truncate">{t.title ?? "New chat"}</span>
                        {(runningThreads.includes(t.thread_id) || t.active_run_id) && <Spinner className="ml-auto shrink-0 motion-reduce:animate-none" aria-label="Answer generating" />}
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </div>
          ))}
        </CollapsibleContent>
      </SidebarGroup>
    </Collapsible>
  );
}

function Nav() {
  const { pod } = usePod();
  const pathname = usePathname();
  const { data: summary } = usePodData<{ unread: number; actionable: number }>(`/v1/pods/${pod}/inbox/summary`, {
    refreshInterval: 5000,
  });
  const { open } = useCommandMenu();
  const item = "h-7 rounded-md px-2 text-[13px] font-normal text-gray-900 [&_svg]:size-[15px] [&_svg]:text-gray-700 hover:bg-sidebar-accent hover:text-gray-1000 data-active:bg-sidebar-accent data-active:font-medium data-active:text-gray-1000 data-active:[&_svg]:text-gray-1000";
  return (
    <SidebarGroup className="pt-0">
      <SidebarMenu className="gap-0.5">
        <SidebarMenuItem>
          <SidebarMenuButton
            tooltip="New chat"
            render={<Link href={`/${pod}/ask`} />}
            className="mb-2 h-7 rounded-md bg-sidebar-accent px-2 text-[13px] font-medium text-gray-1000 hover:bg-sidebar-accent hover:text-gray-1000 [&_svg]:size-[15px] group-data-[collapsible=icon]:bg-transparent"
          >
            <SquarePenIcon />
            <span>New chat</span>
            <Kbd className="ml-auto group-data-[collapsible=icon]:hidden">N</Kbd>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton tooltip="Search (⌘K)" onClick={open} className={item}>
            <SearchIcon />
            <span>Search</span>
            <KbdGroup className="ml-auto group-data-[collapsible=icon]:hidden"><Kbd>⌘</Kbd><Kbd>K</Kbd></KbdGroup>
          </SidebarMenuButton>
        </SidebarMenuItem>
        {NAV.map((n) => {
          const href = `/${pod}/${n.href}`;
          const count = n.href === "inbox" ? summary?.unread ?? 0 : 0;
          return (
            <SidebarMenuItem key={n.href}>
              <SidebarMenuButton tooltip={n.label} isActive={pathname.startsWith(href)} render={<Link href={href} />} className={item}>
                <n.icon />
                <span>{n.label}</span>
                {count > 0 && (
                  <span
                    className={cn(
                      "ml-auto min-w-5 rounded-full px-1.5 text-center text-[11px] font-medium leading-5",
                      summary?.actionable ? "bg-primary text-primary-foreground" : "bg-gray-200 text-gray-900",
                    )}
                    aria-label={`${count} unread`}
                  >
                    {count}
                  </span>
                )}
              </SidebarMenuButton>
            </SidebarMenuItem>
          );
        })}
      </SidebarMenu>
    </SidebarGroup>
  );
}

function NowPlaying() {
  const { current, playing, toggle, chapterTitle } = useCallPlayer();
  const { pod } = usePod();
  const { state } = useSidebar();
  if (!current) return null;
  const href = `/${pod}/calls/${current.call_id}`;
  if (state === "collapsed") {
    return (
      <Link
        href={href}
        aria-label={`Open ${current.company} call`}
        className="mx-auto rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <Cover ticker={current.ticker} callId={current.call_id} size="sm" playing={playing} />
      </Link>
    );
  }
  return (
    <div className="flex items-center gap-2 rounded-md px-1 py-1.5">
      <Link href={href} aria-label={`Open ${current.company} call`} className="shrink-0 rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
        <Cover ticker={current.ticker} callId={current.call_id} size="sm" playing={playing} />
      </Link>
      <div className="min-w-0 flex-1">
        <Link href={href} className="block truncate text-[12px] font-medium text-foreground hover:underline">{current.company}</Link>
        <span className="block truncate text-[10px] text-muted-foreground">{chapterTitle ?? `${current.ticker} · call`}</span>
      </div>
      <Button variant="ghost" size="icon-xs" onClick={toggle} aria-label={playing ? "Pause call" : "Play call"} className="shrink-0 text-muted-foreground hover:text-foreground">
        {playing ? <PauseIcon className="size-3.5" /> : <PlayIcon className="size-3.5" />}
      </Button>
    </div>
  );
}

function Account() {
  const { me, info } = usePod();
  const initials = me.display_name.split(" ").map((s) => s[0]).join("");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" className="h-11 w-full justify-start gap-2.5 px-2 hover:bg-sidebar-accent group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
            <span className="grid size-7 shrink-0 place-items-center rounded-full border border-foreground bg-card text-[11px] font-bold text-foreground">{initials}</span>
            <span className="grid min-w-0 flex-1 leading-tight group-data-[collapsible=icon]:hidden">
              <span className="truncate text-sm">{me.display_name}</span>
              <span className="truncate text-xs text-muted-foreground">{info.display_name}</span>
            </span>
          </Button>
        }
      />
      <DropdownMenuContent side="top" align="start" className="w-60">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="font-normal">
            <div className="text-sm">{me.display_name}</div>
            <div className="text-xs text-muted-foreground">{me.title}</div>
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={signOut}>
          <LogOutIcon /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function MobileTrigger() {
  const { isMobile } = useSidebar();
  if (!isMobile) return null;
  return <SidebarTrigger className="fixed top-3 left-3 z-20 size-8 text-muted-foreground" />;
}

function useAccent(accent: string | undefined) {
  useEffect(() => {
    if (!accent) return;
    document.documentElement.dataset.podAccent = accent;
    return () => {
      delete document.documentElement.dataset.podAccent;
    };
  }, [accent]);
}

export function WorkspaceShell({ pod, children }: { pod: string; children: React.ReactNode }) {
  const { me, pods, loading } = useMe();
  const router = useRouter();
  useAccent(pods?.find((p) => p.pod_id === pod)?.accent);
  if (loading || !me || !pods) return <div className="min-h-screen" />;

  const info = pods.find((p) => p.pod_id === pod);
  if (!info) {
    // Convenience only: the API independently rejects non-members on every call.
    return (
      <div className="grid min-h-screen place-items-center p-8">
        <div className="w-full max-w-sm text-center">
          <p className="text-base font-medium">You don&apos;t have access to this pod</p>
          <p className="mt-1 text-sm text-muted-foreground">Pod access comes from your firm identity.</p>
          <div className="mt-5 grid gap-2">
            {pods.map((p) => (
              <Button key={p.pod_id} variant="outline" onClick={() => router.push(`/${p.pod_id}/ask`)} className="h-10 justify-start gap-2 px-3 text-sm">
                <PodAvatar pod={p} size="sm" /> {p.display_name}
              </Button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <PodContextReact.Provider value={{ pod, info, me, pods }}>
      {/* A fresh cache per pod: switching pods can never render the previous pod's data. */}
      <SWRConfig key={pod} value={{ provider: () => new Map(), revalidateOnFocus: true }}>
        <PodosRunsProvider key={pod} pod={pod}>
        <CallsPlayerProvider>
        <CommandMenuProvider>
        <SidebarProvider style={{ "--sidebar-width": "15rem" } as React.CSSProperties}>
          <Sidebar collapsible="icon" className="border-r">
            <SidebarHeader className="gap-3 px-3 pt-3.5 pb-2">
              <Link href={`/${pod}/today`} className="dossier-brand px-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0" aria-label="PodOS home">
                <DossierMark /><span className="text-[15px] tracking-[-0.02em] group-data-[collapsible=icon]:hidden">PodOS</span>
              </Link>
              <div className="flex items-center gap-1">
                <div className="min-w-0 flex-1"><PodSwitcher /></div>
                <SidebarTrigger className="size-7 text-muted-foreground opacity-0 transition-opacity group-hover/sidebar-wrapper:opacity-100 focus-visible:opacity-100 group-data-[collapsible=icon]:hidden" />
              </div>
            </SidebarHeader>
            <SidebarContent className="gap-3 px-1.5">
              <Nav />
              <Chats />
            </SidebarContent>
            <SidebarFooter className="border-t p-3">
              <NowPlaying />
              <Account />
            </SidebarFooter>
            <SidebarRail />
          </Sidebar>
          <SidebarInset className="min-h-svh bg-background">
            <MobileTrigger />
            {children}
            <PlayerBar />
          </SidebarInset>
        </SidebarProvider>
        </CommandMenuProvider>
        </CallsPlayerProvider>
        </PodosRunsProvider>
      </SWRConfig>
    </PodContextReact.Provider>
  );
}

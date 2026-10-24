"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import useSWR, { useSWRConfig } from "swr";
import { ChevronDownIcon, LoaderCircleIcon, UserIcon } from "lucide-react";
import { DossierMark } from "@/components/dossier-mark";
import { HeroArt } from "@/components/hero-art";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

type DevUser = { subject_id: string; display_name: string; title: string; platform_roles: string[]; pods: string[] };

const AVATAR: Record<string, string> = { "Alpha Capital": "bg-purple-700", "Beta Partners": "bg-green-800" };
const initials = (name: string) => name.split(" ").map((p) => p[0]).join("");

export default function LoginPage() {
  const { data, error } = useSWR<DevUser[]>("/v1/auth/dev-users", (p: string) => api<DevUser[]>(p));
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const router = useRouter();
  const { mutate } = useSWRConfig();

  async function signIn(subject: string) {
    setBusy(subject);
    setErr(null);
    try {
      await api("/v1/auth/dev-login", { body: { subject_id: subject } });
      await mutate(() => true, undefined, { revalidate: false });
      router.replace("/");
    } catch (e) {
      setBusy(null);
      setErr(e instanceof ApiError ? e.message : "Sign-in failed");
    }
  }

  return (
    <main className="relative flex min-h-svh flex-col overflow-hidden bg-[#05050a] text-white antialiased">
      <HeroArt />

      <header className="relative z-10 mx-auto flex w-full max-w-[1200px] items-center px-6 py-6">
        <div className="dossier-brand text-white">
          <DossierMark />
          <span className="text-lg">PodOS</span>
        </div>
      </header>

      <div className="flex-1" />

      <section className="relative z-10 mx-auto w-full max-w-[1200px] px-6 pb-12">
        <p className="font-mono text-[11.5px] tracking-[0.22em] text-white/50 uppercase">Agentic research desk</p>
        <h1 className="mt-4 max-w-[1040px] text-[clamp(38px,6.2vw,76px)] leading-[1.0] font-bold tracking-[-0.04em]">
          Research that <span className="text-white/50">comes to you.</span>
        </h1>
        <p className="mt-5 max-w-xl text-base leading-relaxed text-white/60 md:text-lg">
          Shared agents read, watch and draft across each pod&apos;s private vault — filings, notes and monitors — and keep
          every pod&apos;s research its own.
        </p>

        <div className="mt-9 flex flex-wrap items-center gap-3.5">
          <span className="text-sm text-white/55">Continue as</span>
          <DropdownMenu>
            <DropdownMenuTrigger
              disabled={!!busy}
              className="inline-flex h-12 items-center gap-2.5 rounded-full bg-white pr-4 pl-1.5 text-sm font-semibold text-[#0b0b12] shadow-[0_18px_40px_-18px_rgba(0,0,0,0.85)] outline-none transition-colors hover:bg-white/90 focus-visible:ring-2 focus-visible:ring-white/40 disabled:opacity-70"
            >
              <span className="grid size-9 place-items-center rounded-full bg-[#0b0b12] text-white">
                {busy ? (
                  <LoaderCircleIcon className="size-4 animate-spin motion-reduce:animate-none" />
                ) : (
                  <UserIcon className="size-4" />
                )}
              </span>
              <span>{busy ? "Signing in…" : "Choose an account"}</span>
              <ChevronDownIcon className="size-4 opacity-50" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" sideOffset={8} className="min-w-[280px] p-1.5">
              <DropdownMenuGroup>
                <DropdownMenuLabel>Demo accounts</DropdownMenuLabel>
              </DropdownMenuGroup>
              {error && <p className="px-2 py-3 text-sm text-destructive">Can&apos;t reach the API on :8400.</p>}
              {!data && !error && <p className="px-2 py-3 text-sm text-muted-foreground">Loading accounts…</p>}
              {(data ?? []).map((u) => {
                const pod = u.pods[0]?.split(" · ")[0];
                return (
                  <DropdownMenuItem
                    key={u.subject_id}
                    disabled={!!busy}
                    onClick={() => signIn(u.subject_id)}
                    className="gap-2.5 px-2 py-2"
                  >
                    <span
                      className={cn(
                        "grid size-7 shrink-0 place-items-center rounded-md text-[11px] font-bold text-white",
                        AVATAR[pod ?? ""] ?? "bg-gray-1000",
                      )}
                    >
                      {initials(u.display_name)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{u.display_name}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {u.pods.length
                          ? u.pods.map((p) => p.replace(/ · (?:PM|ANALYST)$/, "")).join(", ")
                          : "Platform admin · no pod access"}
                      </span>
                    </span>
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {err && <p role="alert" className="mt-4 text-sm text-red-300">{err}</p>}
      </section>
    </main>
  );
}

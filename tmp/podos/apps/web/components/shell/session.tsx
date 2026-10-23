"use client";

import { createContext, useContext, useEffect } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { api, ApiError } from "@/lib/api";
import type { Me, Pod } from "@/lib/types";

export function useMe() {
  const router = useRouter();
  const me = useSWR<Me>("/v1/me", (p: string) => api<Me>(p), { shouldRetryOnError: false });
  const pods = useSWR<Pod[]>(me.data ? "/v1/me/pods" : null, (p: string) => api<Pod[]>(p));
  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) router.replace("/login");
  }, [me.error, router]);
  return { me: me.data, pods: pods.data, loading: me.isLoading || (!!me.data && pods.isLoading), mutatePods: pods.mutate };
}

export type PodCtx = { pod: string; info: Pod; me: Me; pods: Pod[] };
export const PodContextReact = createContext<PodCtx | null>(null);

export function usePod(): PodCtx {
  const ctx = useContext(PodContextReact);
  if (!ctx) throw new Error("usePod outside workspace");
  return ctx;
}

export async function signOut() {
  await api("/v1/auth/logout", { method: "POST" });
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- full reload clears in-memory pod caches
  window.location.assign("/login");
}

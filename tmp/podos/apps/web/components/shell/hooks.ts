"use client";

import useSWR, { type SWRConfiguration } from "swr";
import { api } from "@/lib/api";
import { usePod } from "./session";

/** Pod-scoped data hook. The key includes the pod so caches never cross pods. */
export function usePodData<T>(path: string | null, opts?: SWRConfiguration<T>) {
  const { pod } = usePod();
  return useSWR<T>(path ? [path, pod] : null, ([p, pd]: [string, string]) => api<T>(p, { pod: pd }), opts);
}

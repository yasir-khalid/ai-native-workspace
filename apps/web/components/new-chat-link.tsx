"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ComponentProps } from "react";
import { prepareAsk } from "@/lib/ask-handoff";

/** A new draft on every activation, including when already on the chat page. */
export function NewChatLink({ pod, ...props }: Omit<ComponentProps<typeof Link>, "href" | "onNavigate"> & { pod: string }) {
  const router = useRouter();
  return <Link {...props} href={`/${pod}/ask`} onNavigate={event => {
    event.preventDefault();
    router.push(prepareAsk(pod, {}));
  }} />;
}

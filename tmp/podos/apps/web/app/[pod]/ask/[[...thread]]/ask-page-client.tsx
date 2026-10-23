"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { AskView } from "@/components/ask/ask-view";

export default function AskPageClient() {
  const { pod, thread } = useParams<{ pod: string; thread?: string[] }>();
  const router = useRouter();
  const draft = useSearchParams().get("draft");
  const target = thread?.[0] ?? (draft ? `draft-${draft}` : "new");
  const threadId = thread?.[0] ?? null;
  const ownReplace = useRef<string | null>(null);
  const [view, setView] = useState({
    key: target,
    threadId,
    draftId: draft,
    target,
  });

  useEffect(() => {
    // A newly created thread is written to the URL with router.replace; keep that live view mounted.
    if (threadId && ownReplace.current === threadId) {
      ownReplace.current = null;
      return;
    }
    // Any other navigation means the thread we replaced into is no longer the live view.
    ownReplace.current = null;
    setView((prev) => (prev.target === target ? prev : { key: target, threadId, draftId: draft, target }));
  }, [threadId, target, draft]);

  return (
    <AskView
      key={view.key}
      threadId={view.threadId}
      draftId={view.draftId}
      onCreated={(id) => {
        // A request may be accepted after this page has been left or hidden by the router.
        if (window.location.pathname !== `/${pod}/ask`) return;
        ownReplace.current = id;
        setView((v) => ({ ...v, target: id }));
        router.replace(`/${pod}/ask/${id}`);
      }}
    />
  );
}

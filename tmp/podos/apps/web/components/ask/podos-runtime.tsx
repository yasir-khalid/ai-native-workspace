"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AssistantRuntimeProvider, useExternalStoreRuntime, type AppendMessage } from "@assistant-ui/react";
import { useStore } from "zustand";
import { useSWRConfig } from "swr";
import { ChatSessions } from "@/lib/chat-sessions";
import { convert, uid, type AskSelection, type UiMessage } from "@/lib/chat-messages";
export { hydrate, type AskSelection, type UiMessage } from "@/lib/chat-messages";

const SessionsContext = createContext<ChatSessions | null>(null);

export function PodosRunsProvider({ pod, children }: { pod: string; children: ReactNode }) {
  const { mutate } = useSWRConfig();
  const [sessions] = useState(() => new ChatSessions(pod, threadId => {
    void mutate([`/v1/pods/${pod}/threads`, pod]);
    if (threadId) void mutate([`/v1/threads/${threadId}`, pod]);
  }));
  useEffect(() => { sessions.resume(); return () => sessions.dispose(); }, [sessions]);
  return <SessionsContext.Provider value={sessions}>{children}</SessionsContext.Provider>;
}

function useSessions() {
  const sessions = useContext(SessionsContext);
  if (!sessions) throw new Error("Chat runtime requires workspace session provider");
  return sessions;
}
export function useRunningChats() {
  const sessions = useSessions();
  return useStore(sessions.store, state => state.runningThreads);
}

export function PodosRuntimeProvider({ threadId, initialMessages, activeRunId, selection, onThreadCreated, suggestions, children }: {
  pod: string;
  threadId: string | null;
  initialMessages: UiMessage[];
  activeRunId?: string | null;
  selection: AskSelection;
  onThreadCreated: (threadId: string) => void;
  suggestions: { title: string; label?: string; prompt: string }[];
  children: ReactNode;
}) {
  const sessions = useSessions();
  const [key] = useState(() => threadId ?? uid("draft"));
  const current = useStore(sessions.store, state => state.sessions[state.aliases[key] ?? key]);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    sessions.ensure(key, threadId, initialMessages);
    if (activeRunId) sessions.attach(key, activeRunId);
  }, [sessions, key, threadId, initialMessages, activeRunId]);
  const onNew = useCallback(async (message: AppendMessage) => {
    const text = message.content.map(part => part.type === "text" ? part.text : "").join("").trim();
    if (!text) return;
    sessions.ensure(key, threadId, initialMessages);
    await sessions.send(key, text, selection, id => { if (mounted.current) onThreadCreated(id); });
  }, [sessions, key, threadId, initialMessages, selection, onThreadCreated]);
  const onCancel = useCallback(() => sessions.cancel(key), [sessions, key]);
  const runtime = useExternalStoreRuntime<UiMessage>({
    messages: current?.messages ?? initialMessages,
    isRunning: current?.running ?? Boolean(activeRunId),
    convertMessage: convert, onNew, onCancel, suggestions,
  });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}

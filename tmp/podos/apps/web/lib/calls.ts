import { API_URL } from "./api";

export type CallASR = {
  engine: string;
  model: string;
  language: string;
  word_count: number;
  sentence_count: number;
  avg_confidence: number;
  wer_vs_reference?: number;
  reference_words?: number;
};

export type CallSummary = {
  call_id: string;
  company: string;
  ticker: string;
  exchange: string;
  event_title: string;
  event_date: string;
  duration: number;
  tags: string[];
  summary_short: string;
  asr: CallASR;
  chapter_count: number;
  qa_count: number;
  audio_url: string;
};

export type TranscriptWord = {
  w: string;
  start: number;
  end: number;
  p?: number;
  speaker?: string;
};

export type Sentence = {
  id: string;
  text: string;
  start: number;
  end: number;
  confidence?: number;
  paragraph_id: string;
  speaker?: string;
  words?: TranscriptWord[];
};

export type Paragraph = {
  id: string;
  speaker: string;
  start: number;
  end: number;
  sentences: Sentence[];
};

export type Speaker = {
  name: string;
  role: string;
  company: string;
};

export type TranscriptDoc = {
  id: string;
  company: string;
  ticker: string;
  exchange: string;
  event_title: string;
  event_date: string;
  duration: number;
  speakers: Speaker[];
  paragraphs: Paragraph[];
  asr: CallASR;
  summary_short: string;
  summary_long_html: string;
};

export type Chapter = {
  id: string;
  title: string;
  summary: string;
  start: number;
  end: number;
  start_sentence: string;
};

export type QA = {
  id: string;
  speaker: string;
  start: number;
  end: number;
  question_sentence_id: string;
  question: string;
  answer_speaker: string;
  answer_start: number;
  answer_sentence_id: string;
  answer_paragraph_ids: string[];
  answer: string;
};

export type Insight = {
  title: string;
  sentence_ids: string[];
  start: number;
};

export type KeyQuestion = {
  question: string;
  sentence_id: string;
  start: number;
};

export type CallDetail = CallSummary & {
  pod_id?: string;
  audio_path?: string;
  audio_sha256: string;
  transcript: TranscriptDoc;
  chapters: Chapter[];
  qa: QA[];
  insights: Insight[];
  key_questions: KeyQuestion[];
  summary_long_html: string;
  created_at?: string;
};

export type CallCitation = {
  sentence_id: string;
  speaker: string;
  start: number;
  text: string;
};

export function callCitationHref(pod: string, citation: { call_id: string; sentence_id?: string; start?: number }): string {
  const query = new URLSearchParams();
  if (citation.sentence_id) query.set("s", citation.sentence_id);
  if (citation.start !== undefined && Number.isFinite(citation.start) && citation.start >= 0) query.set("t", String(citation.start));
  const path = `/${pod}/calls/${encodeURIComponent(citation.call_id)}`;
  return query.size ? `${path}?${query}` : path;
}

export type AskHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

export type AskEvent =
  | { type: "token"; text: string }
  | { type: "citations"; citations: CallCitation[] }
  | { type: "done" }
  | { type: "error"; message: string };

export const CALL_ASK_SUGGESTIONS = [
  "What were the three biggest takeaways?",
  "What did management say about margins and demand?",
  "Which analyst questions got the most direct answers?",
  "What risks or caveats came up in this call?",
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

export function formatCallDate(value: string, long = false): string {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return long ? `${day} ${MONTHS[month - 1]} ${year}` : `${day} ${MONTHS[month - 1]} ${String(year).slice(2)}`;
}

export function callKind(eventTitle: string): "Earnings" | "Interview" {
  return /earnings call/i.test(eventTitle) ? "Earnings" : "Interview";
}

export function asrModelLabel(model: string): string {
  const tail = model.split("/").pop() ?? model;
  return tail
    .replace(/^whisper-/, "Whisper ")
    .replace(/-/g, " ")
    .replace(/\blarge\b/i, "large")
    .replace(/\bturbo\b/i, "turbo")
    .replace(/\bv3\b/i, "v3")
    .trim();
}

export function audioSrc(audioUrl: string): string {
  return audioUrl.startsWith("http") ? audioUrl : `${API_URL}${audioUrl}`;
}

export function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export async function streamCallAsk(
  callId: string,
  pod: string,
  question: string,
  history: AskHistoryMessage[],
  onEvent: (event: AskEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_URL}/v1/pods/${pod}/calls/${callId}/ask`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-Active-Pod": pod,
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ question, history }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`ask failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let type = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) type = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trimStart();
      }
      if (!data) continue;
      const parsed = JSON.parse(data) as Record<string, unknown>;
      const event = { type, ...parsed } as AskEvent;
      onEvent(event);
      if (type === "done" || type === "error") return;
    }
  }
}

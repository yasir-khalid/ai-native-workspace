#!/usr/bin/env python3
"""Add navigation metadata (chapters, tags, evidence-linked insights) with an LLM."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def load_env() -> None:
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)


def stamp(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def call_llm(prompt: str) -> dict:
    key = os.environ["OPENROUTER_API_KEY"]
    body = json.dumps(
        {
            "model": os.environ.get("PODOS_LLM_MODEL", "anthropic/claude-sonnet-5.5"),
            "temperature": 0.2,
            "max_tokens": 8000,
            "messages": [
                {
                    "role": "system",
                    "content": "You label transcripts for navigation. You never invent facts, figures, or quotes. Reply with JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    text = data["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = text.find("{")
    return json.loads(text[start : text.rfind("}") + 1])


def main() -> None:
    load_env()
    path = Path(sys.argv[1])
    doc = json.loads(path.read_text())
    lines = []
    index = {}
    for p in doc["paragraphs"]:
        for s in p["sentences"]:
            index[s["id"]] = s
            lines.append(f'[{s["id"]} {stamp(s["start"])}] {p["speaker"]}: {s["text"]}')
    transcript = "\n".join(lines)
    prompt = f"""Event: {doc["company"]} ({doc["ticker"]}) — {doc["event_title"]} on {doc["event_date"]}.

Below is the transcript with sentence IDs. Build navigation metadata:

1. "chapters": 8-12 entries covering the whole call in order. Each: {{"title": <=6 words, "start_sentence": "<id>", "summary": <=22 words}}. Boundaries at genuine topic shifts. Quote no numbers not in the text.
2. "tags": 4-8 short topical tags (lowercase, 1-3 words).
3. "insights": 4-6 entries {{"title": <=8 words, "sentence_ids": [2-4 ids]}}. Titles must be supported verbatim by the cited sentences.
4. "key_questions": 5-8 entries {{"question": verbatim question text, "sentence_id": "<id>"}} asked by an interviewer or analyst.

Return one JSON object with keys chapters, tags, insights, key_questions.

TRANSCRIPT
{transcript}
"""
    meta = call_llm(prompt)

    chapters = []
    for i, c in enumerate(meta.get("chapters", [])):
        sid = c.get("start_sentence")
        if sid not in index:
            continue
        chapters.append(
            {
                "id": f"c{i + 1}",
                "title": c.get("title", "Chapter").strip()[:80],
                "summary": (c.get("summary") or "").strip()[:240],
                "start": index[sid]["start"],
                "start_sentence": sid,
            }
        )
    chapters.sort(key=lambda c: c["start"])
    for i, c in enumerate(chapters):
        c["id"] = f"c{i + 1}"
        c["end"] = chapters[i + 1]["start"] if i + 1 < len(chapters) else doc["duration"]

    insights = []
    for ins in meta.get("insights", []):
        ids = [i for i in ins.get("sentence_ids", []) if i in index]
        if not ids:
            continue
        insights.append(
            {
                "title": ins.get("title", "").strip()[:100],
                "sentence_ids": ids,
                "start": min(index[i]["start"] for i in ids),
            }
        )

    questions = []
    for q in meta.get("key_questions", []):
        sid = q.get("sentence_id")
        if sid in index:
            questions.append(
                {
                    "question": q.get("question", "").strip()[:300],
                    "sentence_id": sid,
                    "start": index[sid]["start"],
                }
            )

    doc["chapters"] = chapters
    doc["tags"] = [t.strip()[:40] for t in meta.get("tags", []) if t.strip()]
    doc["insights"] = insights
    doc["key_questions"] = questions
    doc["generated"] = {
        "chapters": True,
        "tags": True,
        "insights": "titles generated; quotes resolved from transcript sentences",
    }
    path.write_text(json.dumps(doc, ensure_ascii=False))
    print(path.name, len(chapters), "chapters", len(doc["tags"]), "tags", len(insights), "insights")
    for c in chapters:
        print(" ", stamp(c["start"]), c["title"])


if __name__ == "__main__":
    main()

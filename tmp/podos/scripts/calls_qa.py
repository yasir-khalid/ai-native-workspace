#!/usr/bin/env python3
"""Derive Q&A exchanges from speaker turns (deterministic, no model)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    doc = json.loads(path.read_text())
    company_root = doc["company"].split()[0].lower()
    company_speakers = set()
    for sp in doc["speakers"]:
        c = (sp.get("company") or "").lower()
        role = (sp.get("role") or "").lower()
        if c and (company_root in c or c in company_root):
            company_speakers.add(sp["name"])
        elif c and any(k in role for k in ("chief", "ceo", "cfo", "founder", "president")):
            company_speakers.add(sp["name"])

    paragraphs = doc["paragraphs"]
    qa = []
    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]
        if p["speaker"] in company_speakers:
            i += 1
            continue
        question = " ".join(s["text"] for s in p["sentences"]).strip()
        q_words = len(question.split())
        if q_words < 4:
            i += 1
            continue
        answers = []
        j = i + 1
        while j < len(paragraphs) and paragraphs[j]["speaker"] in company_speakers:
            answers.append(paragraphs[j])
            j += 1
        if not answers:
            i += 1
            continue
        answer_text = " ".join(
            s["text"] for a in answers for s in a["sentences"]
        ).strip()
        if len(answer_text.split()) < 30:
            i = j
            continue
        qa.append(
            {
                "id": f"q{len(qa) + 1}",
                "speaker": p["speaker"],
                "start": p["start"],
                "end": answers[-1]["end"],
                "question_sentence_id": p["sentences"][-1]["id"],
                "question": question,
                "answer_speaker": answers[0]["speaker"],
                "answer_start": answers[0]["start"],
                "answer_sentence_id": answers[0]["sentences"][0]["id"],
                "answer_paragraph_ids": [a["id"] for a in answers],
                "answer": answer_text,
            }
        )
        i = j

    # Keep the longest exchanges, preserving chronological order.
    if len(qa) > 12:
        keep = sorted(sorted(qa, key=lambda q: len(q["answer"]), reverse=True)[:12], key=lambda q: q["start"])
        qa = keep
    for n, q in enumerate(qa):
        q["id"] = f"q{n + 1}"

    doc["qa"] = qa
    path.write_text(json.dumps(doc, ensure_ascii=False))
    print(f"{path.name}: {len(qa)} Q&A exchanges, company speakers={sorted(company_speakers)}")
    for q in qa[:5]:
        print(f"  {q['start']:.0f}s {q['speaker']}: {q['question'][:80]}")


if __name__ == "__main__":
    main()

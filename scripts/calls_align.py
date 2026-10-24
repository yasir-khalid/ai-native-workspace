#!/usr/bin/env python3
"""Align local ASR output with Quartr speaker metadata and emit transcript JSON.

Usage: python3 scripts/calls_align.py <reference.json> <asr.json> <out.json>
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import re
import sys
from pathlib import Path

NO_SPACE_BEFORE = set(".,!?;:%)]}’”'-")
SENTENCE_END = set(".!?")


def clean_word(w: str) -> str:
    return w.replace("\n", " ").strip()


def join_words(words: list[str]) -> str:
    out = ""
    for w in words:
        if not out:
            out = w
        elif w[:1] in NO_SPACE_BEFORE or out.endswith(("—", "-", "/", "(")):
            out += w
        else:
            out += " " + w
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\b([A-Za-z]) & ([A-Za-z])\b", r"\1&\2", out)
    out = re.sub(r"(?<=\w) -(?=\w)", "-", out)
    out = re.sub(r"(?<=\w)- (?=\w)", "-", out)
    return out


def speak_for_interval(refs: list[dict], starts: list[float], t: float) -> dict:
    i = bisect.bisect_right(starts, t) - 1
    best = None
    best_overlap = 0.0
    for j in (i - 1, i, i + 1):
        if j < 0 or j >= len(refs):
            continue
        r = refs[j]
        overlap = min(r["end"], t + 0.02) - max(r["start"], t - 0.02)
        if overlap > best_overlap:
            best_overlap = overlap
            best = r
    if best is not None:
        return best
    # nearest sentence fallback within 8s
    cand = [r for r in refs if abs((r["start"] + r["end"]) / 2 - t) < 8]
    if cand:
        return min(cand, key=lambda r: abs((r["start"] + r["end"]) / 2 - t))
    return {"speaker": "Speaker", "role": "", "company": "", "start": t, "end": t}


def levenshtein(a: list[str], b: list[str]) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def norm(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return [w for w in text.split() if w]


def main() -> None:
    ref_path, asr_path, out_path = (Path(p) for p in sys.argv[1:4])
    ref = json.loads(ref_path.read_text())
    asr = json.loads(asr_path.read_text())
    duration = float(ref["duration"])

    refs: list[dict] = []
    for turn in ref["turns"]:
        for s in turn["sentences"]:
            start = max(0.0, float(s["start"]))
            end = float(s["end"])
            if end <= start:
                end = start + 0.15
            text = re.sub(r"\s+", " ", s["text"]).strip()
            if not text:
                continue
            refs.append(
                {
                    "speaker": turn["speaker"],
                    "role": turn["role"],
                    "company": turn["company"],
                    "start": start,
                    "end": end,
                    "text": text,
                }
            )
    refs.sort(key=lambda r: r["start"])
    starts = [r["start"] for r in refs]

    # Invalid timing is not evidence that a recognized word was unspoken.
    # Preserve point timestamps for review; do not manufacture a duration.
    words: list[dict] = []
    for seg in asr["segments"]:
        if not seg.get("words"):
            continue
        for w in seg["words"]:
            text = clean_word(w["word"])
            start = float(w["start"])
            end = float(w["end"])
            if (not text or not math.isfinite(start) or not math.isfinite(end)
                    or start < 0 or start >= duration or end < start):
                continue
            src = speak_for_interval(refs, starts, (start + end) / 2)
            words.append(
                {
                    "w": text,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "p": round(float(w.get("probability", 0.0)), 3),
                    "speaker": src["speaker"],
                    **({"timing_quality": "point_estimate"} if end == start else {}),
                }
            )
    # Stable ties retain decoder word order, including point timestamps.
    words.sort(key=lambda w: w["start"])

    # Build sentences, breaking at punctuation, speaker changes, or long pauses.
    sentences: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        text = join_words([w["w"] for w in cur])
        if not re.search(r"[A-Za-z0-9]", text):
            cur = []
            return
        sentences.append(
            {
                "start": cur[0]["start"],
                "end": cur[-1]["end"],
                "text": text,
                "speaker": cur[0]["speaker"],
                "confidence": round(sum(w["p"] for w in cur) / len(cur), 3),
                "words": cur,
            }
        )
        cur = []

    for i, w in enumerate(words):
        if cur:
            prev = cur[-1]
            gap = w["start"] - prev["end"]
            if w["speaker"] != cur[0]["speaker"]:
                flush()
            elif gap > 1.6 and len(cur) >= 4:
                flush()
        cur.append(w)
        ends_sentence = w["w"].rstrip("\"')]”’").endswith(tuple(SENTENCE_END))
        if ends_sentence and len(cur) >= 2:
            nxt = words[i + 1] if i + 1 < len(words) else None
            if nxt is None or nxt["speaker"] != w["speaker"] or (nxt["start"] - w["end"]) > 0.08:
                flush()
    flush()

    # Merge very short fragments into the neighbouring sentence of the same speaker.
    merged: list[dict] = []
    for s in sentences:
        if (
            merged
            and len(norm(s["text"])) < 3
            and merged[-1]["speaker"] == s["speaker"]
            and s["start"] - merged[-1]["end"] < 2.0
        ):
            prev = merged[-1]
            prev["end"] = s["end"]
            prev["text"] = join_words([prev["text"], s["text"]])
            prev["words"] = prev["words"] + s["words"]
            prev["confidence"] = round(
                sum(w["p"] for w in prev["words"]) / len(prev["words"]), 3
            )
        else:
            merged.append(s)

    # Paragraphs group consecutive sentences by speaker.
    speakers: dict[str, dict] = {}
    for turn in ref["turns"]:
        speakers.setdefault(
            turn["speaker"],
            {"name": turn["speaker"], "role": turn["role"], "company": turn["company"]},
        )

    paragraphs: list[dict] = []
    for s in merged:
        if paragraphs and paragraphs[-1]["speaker"] == s["speaker"]:
            paragraphs[-1]["sentences"].append(s)
            paragraphs[-1]["end"] = s["end"]
        else:
            paragraphs.append(
                {
                    "speaker": s["speaker"],
                    "start": s["start"],
                    "end": s["end"],
                    "sentences": [s],
                }
            )

    counter = 0
    for pi, p in enumerate(paragraphs):
        p["id"] = f"p{pi + 1}"
        for s in p["sentences"]:
            counter += 1
            s["id"] = f"s{counter}"
            s["paragraph_id"] = p["id"]

    ref_text = " ".join(r["text"] for r in refs)
    asr_text = " ".join(s["text"] for s in merged)
    ref_words, asr_words = norm(ref_text), norm(asr_text)
    distance = levenshtein(ref_words, asr_words)
    wer = distance / max(1, len(ref_words))

    audio_sha = hashlib.sha256(Path("var/calls", ref["audio_path"]).read_bytes()).hexdigest()
    out = {
        "id": ref["id"],
        "company": ref["company"],
        "ticker": ref["ticker"],
        "exchange": ref.get("exchange"),
        "event_title": ref["event_title"],
        "event_date": ref["event_date"],
        "quarter_label": ref.get("quarter_label"),
        "fiscal_year": ref.get("fiscal_year"),
        "duration": duration,
        "audio_path": ref["audio_path"],
        "audio_url": ref.get("audio_url"),
        "summary_short": ref.get("summary_short"),
        "summary_long_html": ref.get("summary_long_html"),
        "audio_sha256": audio_sha,
        "asr": {
            "engine": "mlx-whisper",
            "model": "mlx-community/whisper-large-v3-turbo",
            "language": asr.get("language", "en"),
            "word_count": len(words),
            "sentence_count": len(merged),
            "avg_confidence": round(sum(w["p"] for w in words) / max(1, len(words)), 3),
            "wer_vs_reference": round(wer, 4),
            "reference_words": len(ref_words),
            "reference_status": "published_reference_not_human_validated_gold",
            "wer_normalizer": "legacy_ascii_v1",
            "point_timestamp_words": sum(w.get("timing_quality") == "point_estimate" for w in words),
            "speaker_attribution": "transferred_from_reference_timing_not_independent_diarization",
        },
        "speakers": list(speakers.values()),
        "paragraphs": paragraphs,
    }
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False))
    print(
        f"{ref['id']}: {len(merged)} sentences, {len(words)} words, "
        f"{len(paragraphs)} paragraphs, wer={wer:.3f}, "
        f"{out_path.stat().st_size / 1024:.0f} KB"
    )


if __name__ == "__main__":
    main()

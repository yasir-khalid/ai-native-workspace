"""Conservative ASR failure diagnostics. Flags never delete or rewrite speech."""

from __future__ import annotations

import re
from collections import Counter


def repetitive_phrase(text: str, minimum: int, fraction: float) -> dict | None:
    tokens = re.findall(r"[a-z0-9']+", text.lower())
    size = 8
    if len(tokens) < size:
        return None
    counts = Counter(tuple(tokens[i:i + size]) for i in range(len(tokens) - size + 1))
    phrase, count = counts.most_common(1)[0]
    share = count / sum(counts.values())
    if count >= minimum and share >= fraction:
        return {"phrase": " ".join(phrase), "occurrences": count, "ngram_share": share}
    return None


def assess_quality(doc: dict) -> dict:
    text = doc.get("text")
    if text is None and "paragraphs" in doc:
        text = " ".join(s["text"] for p in doc["paragraphs"] for s in p["sentences"])
    text = text or ""
    flags = []
    if not text.strip():
        flags.append({"reason": "empty_transcript"})
    global_repeat = repetitive_phrase(text, minimum=8, fraction=0.05)
    if global_repeat:
        flags.append({"reason": "dominant_repeated_phrase", **global_repeat})
    segments = doc.get("segments") or doc.get("sentences") or []
    windows: dict[int, list[dict]] = {}
    for segment in segments:
        window = int(segment.get("seek", float(segment.get("start", 0)) * 100) // 3000)
        windows.setdefault(window, []).append(segment)
        repetition = repetitive_phrase(segment.get("text", ""), minimum=4, fraction=0.1)
        if repetition:
            flags.append({"reason": "segment_repeated_phrase", "start": segment.get("start"),
                          "end": segment.get("end"), **repetition})
    # Whisper can split a runaway decoder window into many short sentence
    # segments. Check the window too, so a brief loop isn't diluted by a long call.
    for window, items in windows.items():
        repetition = repetitive_phrase(" ".join(s.get("text", "") for s in items), minimum=4, fraction=0.1)
        if repetition:
            flags.append({"reason": "window_repeated_phrase", "window_seconds": window * 30,
                          **repetition})
    return {"policy": "repetition_diagnostics_v1", "needs_review": bool(flags), "flags": flags,
            "accuracy_validated": False,
            "note": "Passing these heuristics does not establish transcription accuracy."}

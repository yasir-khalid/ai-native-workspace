#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["jiwer==4.0.0"]
# ///
"""Experimental three-way token vote. Reads ASR candidates, never references.

This baseline-anchored vote is NOT full ROVER. It requires forced alignment and
held-out validation before use for production quotes or playback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from calls_quality import assess_quality
from jiwer import process_words


def words(text: str) -> list[tuple[str, str]]:
    result = []
    for surface in text.split():
        key = "_".join(re.findall(r"[a-z0-9']+", surface.lower()))
        if key:
            result.append((key, surface))
    return result


def vote(texts: list[str]) -> str:
    if len(texts) != 3:
        raise ValueError("exactly three candidates required")
    base = words(texts[0])
    if not base:
        raise ValueError("empty anchor transcript")
    ballots = [[token] for token in base]
    inserts = [[[]] for _ in range(len(base) + 1)]
    for text in texts[1:]:
        other = words(text)
        aligned = process_words(" ".join(key for key, _ in base), " ".join(key for key, _ in other))
        slots = [None] * len(base)
        gaps = [[] for _ in range(len(base) + 1)]
        for op in aligned.alignments[0]:
            if op.type in ("equal", "substitute"):
                for ri, hi in zip(range(op.ref_start_idx, op.ref_end_idx),
                                  range(op.hyp_start_idx, op.hyp_end_idx), strict=True):
                    slots[ri] = other[hi]
            elif op.type == "insert":
                gaps[op.ref_start_idx].extend(other[op.hyp_start_idx:op.hyp_end_idx])
        for i, slot in enumerate(slots):
            ballots[i].append(slot)
        for i, gap in enumerate(gaps):
            inserts[i].append(gap)
    output = []
    for i in range(len(base) + 1):
        gap_votes = Counter(tuple(key for key, _ in gap) for gap in inserts[i])
        winning_gap, count = gap_votes.most_common(1)[0]
        if winning_gap and count >= 2:
            surfaces = next(gap for gap in inserts[i] if tuple(key for key, _ in gap) == winning_gap)
            output.extend(surface for _, surface in surfaces)
        if i == len(base):
            break
        counts = Counter(token[0] if token else None for token in ballots[i])
        winner, count = counts.most_common(1)[0]
        if count < 2:
            winner = base[i][0]  # no consensus: retain the anchor
        if winner is not None:
            output.append(next(token[1] for token in ballots[i] if token and token[0] == winner))
    return " ".join(output)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", nargs=3, type=Path)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new artifact path")
    audio_hash = sha(args.audio)
    docs = [json.loads(path.read_text()) for path in args.candidates]
    for doc in docs:
        if assess_quality(doc)["needs_review"]:
            parser.error("candidate failed repetition/empty-output checks; review it before voting")
        parent_hash = doc.get("experiment", {}).get("audio_sha256")
        if parent_hash and parent_hash != audio_hash:
            parser.error("candidate audio hash mismatch")
    text = vote([doc["text"] for doc in docs])
    result = {"text": text, "experiment": {
        "engine": "three_way_baseline_anchored_vote_v1", "reference_access": False,
        "audio_sha256": audio_hash, "production_promoted": False, "requires_forced_alignment": True,
        "independent_acoustic_families": 2,
        "caveat": "Two Whisper variants are correlated; this is not three independent engines.",
        "parents": [{"path": str(path), "sha256": sha(path), "experiment": doc.get("experiment")}
                    for path, doc in zip(args.candidates, docs, strict=True)],
        "runner_sha256": sha(Path(__file__)),
    }}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False))
    print(args.output)


if __name__ == "__main__":
    main()

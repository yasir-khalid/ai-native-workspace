#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["jiwer==4.0.0", "whisper-normalizer==0.1.15"]
# ///
"""Reproducible reference-disagreement evaluation, never gold accuracy by default.

uv run scripts/calls_evaluate.py MANIFEST --output REPORT
Both historical punctuation-only WER and a pinned Whisper-derived English
normalizer are reported. This is not Artificial Analysis's custom AA-WER.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from calls_quality import assess_quality
from jiwer import process_words
from whisper_normalizer.english import EnglishTextNormalizer


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transcript_text(doc: dict) -> str:
    if "turns" in doc:
        sentences = [s for turn in doc["turns"] for s in turn["sentences"]]
        return " ".join(s["text"] for s in sorted(sentences, key=lambda s: s["start"]))
    if "paragraphs" in doc:
        return " ".join(s["text"] for p in doc["paragraphs"] for s in p["sentences"])
    if isinstance(doc.get("text"), str):
        return doc["text"]
    raise ValueError("unsupported transcript schema")


def legacy_normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


def score(reference: str, hypothesis: str, normalize) -> dict:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref.strip():
        raise ValueError("empty normalized reference")
    result = process_words(ref, hyp)
    errors = Counter()
    review = []
    r, h = result.references[0], result.hypotheses[0]
    for alignment in result.alignments[0]:
        if alignment.type == "equal":
            continue
        a, b = alignment.ref_start_idx, alignment.ref_end_idx
        c, d = alignment.hyp_start_idx, alignment.hyp_end_idx
        expected, actual = " ".join(r[a:b]), " ".join(h[c:d])
        errors[(expected, actual, alignment.type)] += 1
        review.append({
            "type": alignment.type, "reference_token_start": a, "hypothesis_token_start": c,
            "reference": expected, "hypothesis": actual,
            "reference_context": " ".join(r[max(0, a - 8):min(len(r), b + 8)]),
            "contains_digit": bool(re.search(r"\d", expected + actual)),
        })
    return {
        "wer": result.wer, "reference_words": len(r), "hypothesis_words": len(h),
        "substitutions": result.substitutions, "deletions": result.deletions,
        "insertions": result.insertions, "hits": result.hits,
        "top_disagreements": [dict(reference=a, hypothesis=b, type=t, occurrences=n)
                              for (a, b, t), n in errors.most_common(40)],
        "review_items": review,
    }


def evaluate(manifest: dict, root: Path) -> dict:
    english = EnglishTextNormalizer()
    rows = []
    totals: dict[tuple[str, str], Counter] = {}
    for event in manifest["events"]:
        ref_path, audio_path = root / event["reference"], root / event["audio"]
        if digest(ref_path) != event["reference_sha256"] or digest(audio_path) != event["audio_sha256"]:
            raise ValueError(f"artifact hash mismatch: {event['id']}")
        reference = transcript_text(json.loads(ref_path.read_text()))
        for candidate in event["candidates"]:
            path = root / candidate["path"]
            doc = json.loads(path.read_text())
            claimed_audio_hash = doc.get("experiment", {}).get("audio_sha256")
            if claimed_audio_hash and claimed_audio_hash != event["audio_sha256"]:
                raise ValueError(f"candidate belongs to different audio: {candidate['name']}")
            hyp = transcript_text(doc)
            metrics = {}
            for name, normalizer in [("legacy", legacy_normalize), ("whisper_english", english)]:
                metrics[name] = score(reference, hyp, normalizer)
                values = metrics[name]
                total = totals.setdefault((candidate["name"], name), Counter())
                for key in ("reference_words", "hypothesis_words", "substitutions", "deletions", "insertions", "hits"):
                    total[key] += values[key]
                total["events"] += 1
            rows.append({"event_id": event["id"], "candidate": candidate["name"],
                         "candidate_status": candidate.get("status", "experimental_not_promoted"),
                         "path": candidate["path"], "sha256": digest(path),
                         "experiment": doc.get("experiment"), "quality": assess_quality(doc),
                         "metrics": metrics})
    aggregate = []
    for (candidate, normalizer), counts in totals.items():
        wer = (counts["substitutions"] + counts["deletions"] + counts["insertions"]) / counts["reference_words"]
        aggregate.append({"candidate": candidate, "normalizer": normalizer, **counts, "wer": wer,
                          "all_events_evaluated": counts["events"] == len(manifest["events"])})
    # No gold claim or <4% acceptance gate can pass with these unreviewed references.
    return {"created_at": datetime.now(UTC).isoformat(), "reference_status": manifest["reference_status"],
            "validated_gold": False, "target_wer": 0.04, "gold_target_demonstrated": False,
            "aggregation": "sum(S+D+I)/sum(reference_words); never mix partial and full corpus",
            "packages": {p: importlib.metadata.version(p) for p in ["jiwer", "whisper-normalizer"]},
            "normalizer_sha256": digest(Path(__import__('whisper_normalizer.english', fromlist=['']).__file__)),
            "manifest": manifest, "aggregate": aggregate, "results": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("report exists; use a new output path")
    manifest = json.loads(args.manifest.read_text())
    report = evaluate(manifest, args.root)
    report["manifest_sha256"] = digest(args.manifest)
    report["evaluator_sha256"] = digest(Path(__file__))
    report["quality_policy_sha256"] = digest(Path(__file__).with_name("calls_quality.py"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report["aggregate"], indent=2))


if __name__ == "__main__":
    main()

"""Evaluation integrity and recognition-text preservation regressions.

uv run --with pytest --with jiwer==4.0.0 --with whisper-normalizer==0.1.15 pytest tests/test_calls_evaluation.py
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
quality_spec = importlib.util.spec_from_file_location("calls_quality", ROOT / "scripts/calls_quality.py")
quality = importlib.util.module_from_spec(quality_spec)
sys.modules["calls_quality"] = quality
quality_spec.loader.exec_module(quality)
spec = importlib.util.spec_from_file_location("calls_evaluate", ROOT / "scripts/calls_evaluate.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
consensus_spec = importlib.util.spec_from_file_location("calls_consensus", ROOT / "scripts/calls_consensus.py")
consensus = importlib.util.module_from_spec(consensus_spec)
consensus_spec.loader.exec_module(consensus)


def test_normalization_equivalence_does_not_hide_numeric_error():
    normalizer = evaluation.EnglishTextNormalizer()
    assert evaluation.score("We can't pay twenty dollars.", "We can not pay $20.", normalizer)["wer"] == 0
    assert evaluation.score("Revenue is $20 million.", "Revenue is $2 million.", normalizer)["wer"] > 0


def test_error_counts_and_empty_reference():
    result = evaluation.score("a b c d", "a x c e f", evaluation.legacy_normalize)
    assert result["substitutions"] + result["deletions"] + result["insertions"] == 3
    assert result["wer"] == 0.75
    with pytest.raises(ValueError, match="empty normalized reference"):
        evaluation.score("", "hallucination", evaluation.legacy_normalize)


def test_consensus_preserves_negation_numbers_and_anchor_on_disagreement():
    assert consensus.vote(["We did not grow 20 percent.", "We grew 30 percent.",
                           "We did not grow 20 percent."]) == "We did not grow 20 percent."
    assert consensus.vote(["Revenue was 20 million.", "Revenue was 30 million.",
                           "Revenue was 40 million."]) == "Revenue was 20 million."
    assert consensus.vote(["Profit rose.", "Profit did not rise.",
                           "Profit did not rise."]) == "Profit did not rise."


def test_repetition_gate_flags_runaway_output_without_rewriting():
    text = "It's got the driver's license. " * 100
    doc = {"text": text}
    assert quality.assess_quality(doc)["needs_review"] is True
    assert doc["text"] == text
    assert quality.assess_quality({"text": "We expect revenue to rise next quarter."})["needs_review"] is False
    assert quality.assess_quality({"text": ""})["needs_review"] is True
    isolated = {"text": "Ordinary transcript. " * 2000 + text,
                "segments": [{"text": "It's got the driver's license.", "seek": 3000}] * 10}
    assert any(flag["reason"] == "window_repeated_phrase"
               for flag in quality.assess_quality(isolated)["flags"])


def test_consensus_refuses_quarantined_candidate(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    paths = []
    for i, text in enumerate(["Revenue grew.", "It's got the driver's license. " * 100, "Revenue grew."]):
        path = tmp_path / f"candidate-{i}.json"
        path.write_text(json.dumps({"text": text}))
        paths.append(str(path))
    output = tmp_path / "consensus.json"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/calls_consensus.py"), *paths,
                             "--audio", str(audio), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "failed repetition" in result.stderr
    assert not output.exists()


def test_artifact_integrity_and_no_false_gold_gate(tmp_path):
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps({"text": "Revenue is twenty dollars."}))
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"test audio artifact")
    event = {"id": "one", "reference": "reference.json", "audio": "audio.mp3",
             "reference_sha256": evaluation.digest(reference), "audio_sha256": evaluation.digest(audio),
             "candidates": [{"name": "candidate", "path": "reference.json"}]}
    report = evaluation.evaluate({"reference_status": "unreviewed", "events": [event]}, tmp_path)
    assert report["aggregate"][0]["wer"] == 0
    assert report["gold_target_demonstrated"] is False
    assert report["validated_gold"] is False
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"text": "Revenue is twenty dollars.",
                                "experiment": {"audio_sha256": "wrong"}}))
    event["candidates"] = [{"name": "wrong", "path": "wrong.json"}]
    with pytest.raises(ValueError, match="different audio"):
        evaluation.evaluate({"reference_status": "unreviewed", "events": [event]}, tmp_path)
    reference.write_text('{"text":"changed"}')
    with pytest.raises(ValueError, match="hash mismatch"):
        evaluation.evaluate({"reference_status": "unreviewed", "events": [event]}, tmp_path)


def test_alignment_keeps_point_timestamp_words_in_decoder_order(tmp_path):
    audio = tmp_path / "var/calls/audio"
    audio.mkdir(parents=True)
    (audio / "test.mp3").write_bytes(b"audio")
    reference = {"id": "test", "company": "Example", "ticker": "EX", "event_title": "Event",
                 "event_date": "2026-10-23", "duration": 5, "audio_path": "audio/test.mp3",
                 "turns": [{"speaker": "Name", "role": "CFO", "company": "Example", "sentences": [
                     {"text": "The gross margin increased.", "start": 0, "end": 4}]}]}
    words = [{"word": word, "start": start, "end": end, "probability": 0.9}
             for word, start, end in [("The", 0, .5), ("gross", 1, 1.5), ("margin", 1, 1),
                                      ("increased.", 2, 3), ("backward", 4, 3), ("", 3, 4)]]
    (tmp_path / "ref.json").write_text(json.dumps(reference))
    (tmp_path / "asr.json").write_text(json.dumps({"segments": [{"words": words}]}))
    subprocess.run([sys.executable, str(ROOT / "scripts/calls_align.py"), "ref.json", "asr.json", "out.json"],
                   cwd=tmp_path, check=True, capture_output=True)
    result = json.loads((tmp_path / "out.json").read_text())
    assert evaluation.transcript_text(result) == "The gross margin increased."
    assert result["asr"]["point_timestamp_words"] == 1
    point = result["paragraphs"][0]["sentences"][0]["words"][2]
    assert point["start"] == point["end"] == 1
    assert point["timing_quality"] == "point_estimate"

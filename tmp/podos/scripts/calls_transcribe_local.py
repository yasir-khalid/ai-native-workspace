#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mlx-whisper==0.4.3", "parakeet-mlx==0.5.3"]
# ///
"""Independent local ASR experiments. Never reads evaluation references.

uv run scripts/calls_transcribe_local.py AUDIO OUTPUT --engine parakeet
The output is a candidate artifact, not an automatic production replacement.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from calls_quality import assess_quality


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--engine", choices=["parakeet", "whisper"], required=True)
    parser.add_argument("--model")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--decode-policy", choices=["resilient", "deterministic-carry"], default="resilient")
    parser.add_argument("--prompt-file", type=Path, help="Independent document vocabulary, never a reference transcript")
    args = parser.parse_args()
    runner_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.output.exists():
        parser.error("output exists; use a new artifact path")
    if args.engine == "parakeet" and args.prompt_file:
        parser.error("this Parakeet adapter does not support vocabulary prompts")
    model_id = args.model or (
        "mlx-community/parakeet-tdt-0.6b-v3" if args.engine == "parakeet"
        else "mlx-community/whisper-large-v3-turbo"
    )
    prompt = args.prompt_file.read_text() if args.prompt_file else None
    import mlx.core as mx

    mx.random.seed(args.seed)
    from huggingface_hub import hf_hub_download, snapshot_download

    config_path = Path(hf_hub_download(model_id, "config.json"))
    revision = config_path.parent.name
    # Freeze the resolved snapshot; adapters must not resolve a moving main twice.
    snapshot = snapshot_download(model_id, revision=revision)
    started = time.monotonic()
    if args.engine == "parakeet":
        from parakeet_mlx import from_pretrained

        model = from_pretrained(snapshot)
        result = model.transcribe(
            str(args.audio), chunk_duration=120, overlap_duration=15,
            chunk_callback=lambda position, total: print(f"ASR {position / total:.0%}", flush=True),
        )
        output = asdict(result)
        package = "parakeet-mlx"
        params = {"chunk_duration": 120, "overlap_duration": 15, "decoding": "greedy"}
    else:
        import mlx_whisper

        resilient = args.decode_policy == "resilient"
        params = {"language": "en", "word_timestamps": True,
                  "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0) if resilient else 0.0,
                  "condition_on_previous_text": not resilient,
                  "initial_prompt": prompt, "verbose": False}
        output = mlx_whisper.transcribe(str(args.audio), path_or_hf_repo=snapshot, **params)
        package = "mlx-whisper"
    output["experiment"] = {
        "engine": args.engine, "model": model_id,
        "model_revision": revision,
        "random_seed": args.seed,
        "package": package, "package_version": importlib.metadata.version(package),
        "parameters": params, "audio_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest() if prompt else None,
        "prompt_path": str(args.prompt_file) if args.prompt_file else None,
        "runner_sha256": runner_hash,
        "created_at": datetime.now(UTC).isoformat(), "elapsed_seconds": round(time.monotonic() - started, 3),
        "reference_access": False, "production_promoted": False,
    }
    output["quality"] = assess_quality(output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False))
    print(json.dumps({"output": str(args.output), "experiment": output["experiment"]}), flush=True)
    if output["quality"]["needs_review"]:
        print("Candidate requires review: repetition/empty-output gate failed", flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    main()

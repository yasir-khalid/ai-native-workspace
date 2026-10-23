"""Seed the call library from prepared transcript packs into every pod."""

from __future__ import annotations

import asyncio
import json
from datetime import date

from .db import Database, Jsonb, fetchall
from .settings import ROOT, Settings, get_settings

TRANSCRIPTS = ROOT / "var" / "calls" / "transcripts"


async def seed_calls(settings: Settings | None = None) -> dict[str, int]:
    s = settings or get_settings()
    db = Database(s.database_url, max_size=2)
    await db.open()
    counts = {"pods": 0, "transcripts": 0, "upserted": 0}
    try:
        files = sorted(TRANSCRIPTS.glob("*.json"))
        async with db.system_tx() as conn:
            pods = await fetchall(conn, "SELECT pod_id FROM pods ORDER BY pod_id")
        counts["pods"] = len(pods)
        counts["transcripts"] = len(files)
        for pod in pods:
            async with db.pod_tx(pod["pod_id"]) as conn:
                for path in files:
                    data = json.loads(path.read_text())
                    event_date = date.fromisoformat(data["event_date"]) if data.get("event_date") else None
                    await conn.execute(
                        """INSERT INTO call_events(
                             pod_id, call_id, company, ticker, exchange, event_title, event_date, duration,
                             audio_path, audio_sha256, transcript, chapters, qa, insights, key_questions, tags,
                             summary_short, summary_long_html, asr)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (pod_id, call_id) DO UPDATE SET
                             company = excluded.company, ticker = excluded.ticker, exchange = excluded.exchange,
                             event_title = excluded.event_title, event_date = excluded.event_date,
                             duration = excluded.duration, audio_path = excluded.audio_path,
                             audio_sha256 = excluded.audio_sha256, transcript = excluded.transcript,
                             chapters = excluded.chapters, qa = excluded.qa, insights = excluded.insights,
                             key_questions = excluded.key_questions, tags = excluded.tags,
                             summary_short = excluded.summary_short, summary_long_html = excluded.summary_long_html,
                             asr = excluded.asr""",
                        (
                            pod["pod_id"], data["id"], data["company"], data.get("ticker"), data.get("exchange"),
                            data["event_title"], event_date, float(data["duration"]), data["audio_path"],
                            data.get("audio_sha256"), Jsonb(data), Jsonb(data.get("chapters") or []),
                            Jsonb(data.get("qa") or []), Jsonb(data.get("insights") or []),
                            Jsonb(data.get("key_questions") or []), data.get("tags") or [],
                            data.get("summary_short"), data.get("summary_long_html"),
                            Jsonb(data["asr"]) if data.get("asr") is not None else None,
                        ),
                    )
                    counts["upserted"] += 1
    finally:
        await db.close()
    return counts


if __name__ == "__main__":
    print(asyncio.run(seed_calls()))

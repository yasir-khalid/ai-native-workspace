"""Generative UI for agent answers (OpenUI Lang, https://www.openui.com).

* LLM path: ``prompt_section()`` is appended to the system prompt. It is generated from the web app's
  component spec (apps/web/scripts/openui-prompt.mjs -> config/openui/system-prompt.md), so the agent
  is only ever taught components the chat can render. ``PODOS_GENUI=off`` disables it.
* Deterministic path: small builders that turn numbers actually present in the retrieved sources into an
  ```openui-lang block (never invented values), so the no-LLM mode and the tests get real charts too.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPT_PATH = Path(__file__).resolve().parents[2] / "config" / "openui" / "system-prompt.md"
VISUAL_RE = re.compile(r"\b(chart|graph|plot|visuali[sz]e|visual|table|compare|comparison|scenarios?|breakdown|dashboard)\b", re.I)
_NUM = r"(\d+(?:\.\d+)?)"


def enabled() -> bool:
    return os.environ.get("PODOS_GENUI", "on") != "off" and PROMPT_PATH.exists()


@lru_cache(maxsize=1)
def _prompt_text() -> str:
    text = PROMPT_PATH.read_text()
    return text.split("-->", 1)[1].strip() if text.startswith("<!--") else text


def prompt_section() -> str:
    if not enabled():
        return ""
    return "\n\n## Visualizations (optional)\n" + _prompt_text()


def wants_visual(question: str) -> bool:
    return bool(VISUAL_RE.search(question))


def _s(text: str) -> str:
    """OpenUI Lang string literal (double quotes, backslash escapes)."""
    return json.dumps(text, ensure_ascii=False)


def fence(program: str) -> str:
    return f"\n```openui-lang\n{program.strip()}\n```\n"


def scenario_block(results: list[dict[str, Any]]) -> str | None:
    """Bear / base / bull EPS from a source that states all three, e.g. "EPS 6.85. Bear case EPS 4.10 ... Bull case EPS 8.20"."""
    for i, r in enumerate(results):
        body = r.get("body", "")
        bear = re.search(rf"bear case EPS {_NUM}", body, re.I)
        bull = re.search(rf"bull case EPS {_NUM}", body, re.I)
        base = re.search(rf"(?<!case )\bEPS {_NUM}", body)
        if not (bear and bull and base):
            continue
        year = re.search(r"\b(FY\d{2})\b", body)
        label = f"{year.group(1)} EPS" if year else "EPS"
        ticker = r.get("ticker") or ""
        n = i + 1
        follow = [f"What drives the {ticker} bull case?".replace("  ", " "), f"Stress-test the {ticker} bear case".replace("  ", " ")]
        return fence("\n".join([
            "root = Stack([card, follow])",
            "card = Card([header, chart, note])",
            f"header = CardHeader({_s(f'{ticker} {label} by scenario [{n}]'.strip())})",
            f"chart = BarChart([\"Bear\", \"Base\", \"Bull\"], [Series({_s(label)}, [{bear.group(1)}, {base.group(1)}, {bull.group(1)}])])",
            f"note = TextContent({_s(f'Source: {r.get('title', 'model assumptions')} [{n}]')}, \"small\")",
            "follow = Buttons([" + ", ".join(f"Button({_s(q)}, @ToAssistant({_s(q)}))" for q in follow) + "])",
        ]))
    return None


def sources_table_block(results: list[dict[str, Any]], title: str) -> str:
    rows = results[:6]
    titles = ", ".join(_s(f"{r.get('title', '')} [{i + 1}]") for i, r in enumerate(rows))
    kinds = ", ".join(_s((r.get("kind") or "").capitalize()) for r in rows)
    dates = ", ".join(_s(str(r.get("published_at", ""))[:10]) for r in rows)
    return fence("\n".join([
        "root = Stack([card])",
        "card = Card([header, table])",
        f"header = CardHeader({_s(title)})",
        f"table = Table([Col(\"Source\", [{titles}]), Col(\"Type\", [{kinds}]), Col(\"Date\", [{dates}])])",
    ]))


def block_for(question: str, results: list[dict[str, Any]], subject: str) -> str:
    """The deterministic visualization for a question that asked for one ('' when it didn't)."""
    if not wants_visual(question) or not results:
        return ""
    return scenario_block(results) or sources_table_block(results, f"{subject}: sources in the vault")

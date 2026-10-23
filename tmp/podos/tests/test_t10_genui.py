"""T10: generative UI (OpenUI Lang) in agent answers.

Blocks are validated with the real OpenUI parser (apps/web/scripts/openui-validate.mjs) against the PodOS
component subset, and every charted number must come from a cited source in the asking pod.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from conftest import ROOT, text_of

WEB = ROOT / "apps" / "web"
needs_node = pytest.mark.skipif(not shutil.which("node") or not (WEB / "node_modules" / "@openuidev").exists(),
                                reason="node + apps/web dependencies required")


def blocks(text: str) -> list[str]:
    return re.findall(r"```openui-lang\n(.*?)\n```", text, re.S)


def validate(programs: list[str]) -> list[dict]:
    out = subprocess.run(["node", "scripts/openui-validate.mjs"], cwd=WEB, input=json.dumps(programs),
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def completed(ev: list[dict]) -> dict:
    return [e for e in ev if e["type"] == "run.completed"][0]


@needs_node
async def test_chart_request_renders_valid_grounded_chart(alice):
    _, ev = await alice.ask("pod_alpha", "Chart the NWSC EPS scenarios")
    text = text_of(ev)
    [prog] = blocks(text)
    [res] = validate([prog])
    assert res["ok"], res
    assert "BarChart" in res["components"] and not {"Image", "ImageBlock", "ListItem"} & set(res["components"])
    nums = re.search(r"Series\([^,]+, \[([^\]]+)\]\)", prog).group(1)
    ref = int(re.search(r"\[(\d+)\]", prog).group(1))
    cited = completed(ev)["citations"][ref - 1]
    doc = (await alice.get(f"/v1/pods/pod_alpha/documents/{cited['doc_id']}")).json()
    for n in [x.strip() for x in nums.split(",")]:
        assert n in doc["body"], f"{n} is not in the cited source"


async def test_plain_question_has_no_visualization(alice):
    _, ev = await alice.ask("pod_alpha", "What is our NWSC thesis?")
    assert blocks(text_of(ev)) == []


@needs_node
async def test_beta_chart_never_uses_alpha_numbers(ben):
    _, ev = await ben.ask("pod_beta", "Chart the NWSC EPS scenarios")
    text = text_of(ev)
    assert "6.85" not in text and "4.10" not in text and "8.20" not in text
    progs = blocks(text)
    if progs:
        assert all(r["ok"] for r in validate(progs))


@needs_node
def test_agent_prompt_is_generated_from_the_ui_spec():
    r = subprocess.run(["node", "scripts/openui-prompt.mjs", "--check"], cwd=WEB, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    prompt = (ROOT / "config" / "openui" / "system-prompt.md").read_text()
    assert "BarChart(" in prompt and "Image(" not in prompt and '@OpenUrl("' not in prompt


def test_genui_can_be_turned_off(monkeypatch):
    from podos_agents import genui
    assert "openui-lang" in genui.prompt_section()
    monkeypatch.setenv("PODOS_GENUI", "off")
    assert genui.prompt_section() == ""

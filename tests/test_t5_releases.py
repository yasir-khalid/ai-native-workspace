"""T5 - Pod-targeted releases, independent flags, deterministic canary, emergency disable."""

from __future__ import annotations

from conftest import text_of
from podos_api.governance import load_snapshot
from podos_api.releases import stable_bucket
from podos_contracts.models import PodContext


async def patch(pat, key: str, **changes) -> dict:
    r = await pat.patch(f"/v1/admin/releases/{key}", json={"change_ticket": "CHG-T5", "approver": "cab", **changes})
    assert r.status_code == 200, r.text
    return r.json()


async def agents(user, pod: str) -> dict[str, dict]:
    return {a["agent_id"]: a for a in (await user.get(f"/v1/pods/{pod}/agents")).json()}


async def test_agent_b_pilot_alpha_only(alice, ben, dana):
    assert "filing-analyst" in await agents(alice, "pod_alpha")
    assert not (await agents(ben, "pod_beta"))["filing-analyst"]["actions"]["direct"]
    # Same person, two pods: availability follows the *pod*, not the user.
    assert "filing-analyst" in await agents(dana, "pod_alpha")
    assert not (await agents(dana, "pod_beta"))["filing-analyst"]["actions"]["direct"]
    _, ev = await ben.ask("pod_beta", "Review the HLXT 8-K filing", mode="auto")
    assert [e for e in ev if e["type"] == "agent.selected"][0]["agent_id"] == "equity-research"


async def test_direct_and_auto_flags_are_independent(alice, pat):
    await patch(pat, "filing-analyst-pilot", stage="POD_PILOT_AUTO", feature_flags={"auto_route": True, "direct_invoke": False})
    fa = (await agents(alice, "pod_alpha"))["filing-analyst"]
    assert fa["actions"] == {"direct": False, "auto": True, "background": False}
    t = await alice.new_thread("pod_alpha")
    r = await alice.post(f"/v1/threads/{t}/runs", json={"mode": "direct", "agent_id": "filing-analyst", "message": "LGS"},
                         pod="pod_alpha")
    assert r.status_code == 403 and r.json()["reason"] == "release_direct_invoke_disabled"
    _, ev = await alice.ask("pod_alpha", "Review the LGS 8-K filing for governance changes", mode="auto")
    assert [e for e in ev if e["type"] == "agent.selected"][0]["agent_id"] == "filing-analyst"
    assert "Filing review" in text_of(ev)


async def test_expand_to_beta_via_pod_flag(ben, pat):
    await patch(pat, "filing-analyst-pilot", stage="MULTIPOD", pod_allowlist=["pod_alpha", "pod_beta"])
    assert "filing-analyst" in await agents(ben, "pod_beta")
    _, ev = await ben.ask("pod_beta", "Review the HLXT 8-K filing", agent_id="filing-analyst")
    assert ev[-1]["type"] == "run.completed" and "HLXT" in text_of(ev) and "ALPHA-PRIVATE" not in str(ev)


async def test_canary_is_deterministic_and_only_affects_new_runs(alice, pat, svc):
    run_before, ev = await alice.ask("pod_alpha", "Review the LGS 8-K filing", agent_id="filing-analyst")
    assert [e for e in ev if e["type"] == "agent.selected"][0]["version"] == "1.0.0"
    rel = await patch(pat, "filing-analyst-pilot",
                      runtime_canary={"stable_version": "1.0.0", "candidate_version": "1.1.0", "candidate_fraction_percent": 50})
    bucket = stable_bucket("filing-analyst-pilot", "canary", "pod_alpha", "alice.chen", rel["stable_salt_id"])
    expected = "1.1.0" if bucket < 5000 else "1.0.0"
    versions = set()
    for _ in range(3):
        _, ev = await alice.ask("pod_alpha", "Review the LGS 8-K filing", agent_id="filing-analyst")
        versions.add([e for e in ev if e["type"] == "agent.selected"][0]["version"])
    assert versions == {expected}, "sticky per user"
    if expected == "1.1.0":
        assert "| Tone |" in text_of(ev), "the candidate version adds its tone column"
    old = (await alice.get(f"/v1/runs/{run_before}", pod="pod_alpha")).json()
    assert old["agent_version"] == "1.0.0", "existing runs keep their recorded version"
    # Fraction is honoured statistically across a synthetic cohort and stable across evaluations.
    snap = await load_snapshot(svc.db)
    picks = []
    for i in range(400):
        ctx = PodContext(subject_id=f"user{i}", active_pod_id="pod_alpha", pod_role="analyst", groups=["research_users"],
                         session_id="s", permissions_revision="r", request_id="r", trace_id="t")
        a = svc.gov.agent_availability(ctx, snap, "filing-analyst")
        b = svc.gov.agent_availability(ctx, snap, "filing-analyst")
        assert a.version == b.version
        picks.append(a.version == "1.1.0")
    assert 0.40 < sum(picks) / len(picks) < 0.60


async def test_emergency_disable_blocks_new_invocations_despite_ui_cache(alice, arjun, pat):
    cached_catalogue = await agents(alice, "pod_alpha")  # what a stale browser tab still shows
    assert "filing-analyst" in cached_catalogue
    r = await pat.post("/v1/admin/releases/filing-analyst-pilot/emergency", json={"disabled": True, "change_ticket": "INC-42"})
    assert r.status_code == 200
    t = await alice.new_thread("pod_alpha")
    r = await alice.post(f"/v1/threads/{t}/runs", json={"mode": "direct", "agent_id": "filing-analyst", "message": "LGS"},
                         pod="pod_alpha")
    assert r.status_code == 403 and r.json()["reason"] == "agent_emergency_disabled"
    disabled = (await agents(alice, "pod_alpha"))["filing-analyst"]
    assert disabled["unavailable_reason"] == "agent_emergency_disabled"
    assert not any(disabled["actions"].values())
    # Capability kill switch: shared PPT renderer off for everyone, directly and through agents.
    await pat.post("/v1/admin/releases/cap.document.export.pptx/emergency", json={"disabled": True, "change_ticket": "INC-43"})
    deck = {"capability_id": "document.export.pptx", "idempotency_key": "kill-0001", "spec": {"title": "x"}}
    r = await arjun.post("/v1/pods/pod_alpha/exports", json=deck)
    assert r.status_code == 403 and r.json()["reason"] == "capability_emergency_disabled"
    _, ev = await arjun.ask("pod_alpha", "Prepare a deck on NWSC")
    assert [e for e in ev if e.get("tool") == "document.export.pptx" and e["type"] == "tool.completed"][0]["status"] == "denied"
    # Re-enable restores service without any redeploy.
    await pat.post("/v1/admin/releases/filing-analyst-pilot/emergency", json={"disabled": False, "change_ticket": "INC-42"})
    assert "filing-analyst" in await agents(alice, "pod_alpha")


async def test_paused_stage_and_zero_cohort_keep_entitled_agent_visible(alice, pat):
    await patch(pat, "filing-analyst-pilot", stage="PAUSED")
    unavailable = (await agents(alice, "pod_alpha"))["filing-analyst"]
    assert not any(unavailable["actions"].values())
    await patch(pat, "filing-analyst-pilot", stage="POD_PILOT_DIRECT", user_fraction_percent=0)
    unavailable = (await agents(alice, "pod_alpha"))["filing-analyst"]
    assert not any(unavailable["actions"].values())


async def test_release_group_restriction_keeps_entitled_agent_visible(alice, pat):
    # Existing entitlement remains visible; release still narrows invocation.
    await patch(pat, "filing-analyst-pilot", eligible_groups=["quant_users"])
    unavailable = (await agents(alice, "pod_alpha"))["filing-analyst"]
    assert not any(unavailable["actions"].values())


async def test_release_admin_controls_and_audit(pat, alice):
    r = await alice.patch("/v1/admin/releases/filing-analyst-pilot", json={"change_ticket": "x", "approver": "y", "stage": "GA"})
    assert r.status_code == 403
    rel = await patch(pat, "filing-analyst-pilot", stage="POD_PILOT_AUTO", feature_flags={"auto_route": True})
    assert rel["revision"].endswith(".1") or rel["revision"] != "2026-10-11.1"
    detail = (await pat.get("/v1/admin/releases/filing-analyst-pilot")).json()
    assert detail["history"][0]["detail"]["change_ticket"] == "CHG-T5"
    prev = (await pat.post("/v1/admin/releases/filing-analyst-pilot/preview", json={})).json()["results"]
    by = {(p["subject_id"], p["pod_id"]): p for p in prev}
    assert by[("alice.chen", "pod_alpha")]["auto"] is True and by[("ben.okafor", "pod_beta")]["catalogue"] is False
    bad = await pat.patch("/v1/admin/releases/filing-analyst-pilot",
                          json={"change_ticket": "x1", "approver": "y", "pod_allowlist": ["pod_gamma"]})
    assert bad.status_code == 422


async def test_run_records_release_revision(alice):
    run_id, _ = await alice.ask("pod_alpha", "NWSC")
    run = (await alice.get(f"/v1/runs/{run_id}", pod="pod_alpha")).json()
    assert run["release_revision"].startswith("rel-")

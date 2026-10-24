"""The external demo exposes only its two research users, including after reseeding."""

from dataclasses import replace

import httpx
from conftest import BASE, ROOT, login
from podos_api.db import Jsonb
from podos_api.seed import seed
from podos_api.settings import get_settings


async def test_demo_seed_retires_extra_accounts_and_preserves_pod_content(alice, pat, svc):
    before = (await alice.get("/v1/pods/pod_alpha/research-cases")).json()
    for pod in ("pod_alpha", "pod_beta"):
        async with svc.db.pod_tx(pod) as conn:
            await conn.execute(
                """INSERT INTO call_events(pod_id, call_id, company, event_title, duration, audio_path, transcript)
                   VALUES (%s, %s, 'Example', 'Earnings call', 60, 'test.mp3', %s)""",
                (pod, f"{pod}-call", Jsonb({"paragraphs": []})),
            )
    # Run the real two-account demo seed over an existing, populated directory.
    await seed(replace(get_settings(), config_dir=ROOT / "config"))
    async with httpx.AsyncClient(base_url=BASE) as client:
        users = (await client.get("/v1/auth/dev-users")).json()
        assert {u["subject_id"] for u in users} == {"yasir.khalid", "bianca.rossi"}
        assert all(not u["platform_roles"] for u in users)
        for subject in ("alice.chen", "arjun.patel", "ben.okafor", "dana.kim", "pat.morgan"):
            assert (await client.post("/v1/auth/dev-login", json={"subject_id": subject})).status_code == 401
    assert (await alice.get("/v1/me")).status_code == 401
    assert (await pat.get("/v1/me")).status_code == 401
    for subject, own, other in [("yasir.khalid", "pod_alpha", "pod_beta"),
                                 ("bianca.rossi", "pod_beta", "pod_alpha")]:
        user = await login(subject)
        try:
            assert (await user.get("/v1/me")).json()["is_admin"] is False
            assert [p["pod_id"] for p in (await user.get("/v1/me/pods")).json()] == [own]
            reports = await user.get(f"/v1/pods/{own}/research-cases")
            assert reports.status_code == 200 and reports.json()
            if own == "pod_alpha":
                assert {c["case_id"] for c in reports.json()} == {c["case_id"] for c in before}
            calls = await user.get(f"/v1/pods/{own}/calls")
            assert calls.status_code == 200 and len(calls.json()) == 1
            for resource in ("research-cases", "calls", "threads"):
                assert (await user.get(f"/v1/pods/{other}/{resource}")).status_code == 403
            assert (await user.get("/v1/admin/access/people")).status_code == 403
        finally:
            await user.http.aclose()

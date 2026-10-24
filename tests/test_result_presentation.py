"""Structured A2A data stays factual, bounded and safe to render."""

from podos_api.a2a_adapter import _GenericStream
from podos_api.presentation import court_result, public_url

COURTS = [{
    "venue": "Example Sports Centre", "organization": "Example operator", "address": "1 Example Road, London",
    "distance": 1.2, "price": "£12.80", "date": "Sun, Oct 25", "healthcheck": "deprecated", "last_refreshed": "2026-10-24T13:00:00Z",
    "availability": [{"startingTime": "17:00", "endingTime": "18:00", "price": "£12.80",
                      "available": True, "bookingUrl": "https://booking.example.com/court?date=2026-10-25"}],
}]


def test_court_data_keeps_source_values_and_rejects_unsafe_links():
    result = court_result(COURTS)
    assert result["venues"][0]["slots"][0]["price_gbp"] == 12.8
    assert result["venues"][0]["distance_miles"] == 1.2
    assert result["venues"][0]["stale"] is True
    missing_price = court_result([{**COURTS[0], "availability": [{**COURTS[0]["availability"][0], "price": None}]}])
    assert missing_price["venues"][0]["slots"][0]["price_gbp"] is None
    assert missing_price["venues"][0]["price_from_gbp"] == 12.8
    for price, expected in [(12.8, 12.8), ("£1,200.00", 1200), ("from £12.80", None), ("£12.80/hour", None)]:
        sample = court_result([{**COURTS[0], "availability": [{**COURTS[0]["availability"][0], "price": price}]}])
        assert sample["venues"][0]["slots"][0]["price_gbp"] == expected
    assert court_result([{"venue": "not enough information"}]) is None
    assert court_result({"venues": COURTS}) is None
    for url in ["javascript:alert(1)", "https://127.0.0.1/x", "https://localhost/x", "https://internal.local/x",
                "https://user:password@example.com/x", "https://10.0.0.1/x", "https://[::1]/x"]:
        assert public_url(url) is None
    assert public_url("https://booking.example.com/court?date=2026-10-25") is not None


def test_a2a_array_artifact_becomes_one_result_even_with_final_task_snapshot():
    stream = _GenericStream()
    artifact = {"artifactId": "court-result", "name": "court_availability", "parts": [{"data": COURTS}]}
    initial = stream.feed({"artifactUpdate": {"artifact": artifact}})
    results = [event for kind, event in initial if kind == "event" and event["type"] == "ui.result"]
    assert len(results) == 1 and results[0]["result"]["venues"][0]["name"] == "Example Sports Centre"
    repeated = stream.feed({"task": {"artifacts": [artifact], "status": {"state": "TASK_STATE_COMPLETED"}}})
    assert not [event for kind, event in repeated if kind == "event" and event.get("type") == "ui.result"]

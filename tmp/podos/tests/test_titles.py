"""Chat titles: the first message becomes a short (<=4 word) sidebar title, never the raw question."""

from __future__ import annotations

from podos_api.titles import short_title


def test_titles_are_at_most_four_words():
    message = "Find the most recent SEC filing and summarise the key risk disclosures."
    title = short_title(message)
    assert title.split() and len(title.split()) <= 4
    assert title != message


def test_titles_track_the_topic():
    assert short_title("Find the most recent SEC filing and summarise the key risk disclosures.") == "Recent SEC Filing Risk"
    assert short_title("Chart HLXT peak sales scenarios as a bar chart.") == "HLXT Peak Sales Scenarios"
    assert short_title("Summarise our HLXT thesis, target and the key risks.") == "HLXT Thesis Target Risks"


def test_titles_drop_conversational_lead():
    assert short_title("hello, can you find me bloomberg's recent SEC filings?") == "Bloomberg's Recent SEC Filings"


def test_titles_preserve_tickers():
    assert "NVDA" in short_title("Compare NVDA and AMD valuations ahead of earnings next week.")


def test_titles_trim_weak_trailing_words():
    assert short_title("Compare NVDA and AMD valuations ahead of earnings next week.") == "NVDA AMD Valuations"


def test_titles_fall_back_for_empty_message():
    assert short_title("") == "New chat"
    assert short_title("   ...   ") == "New chat"

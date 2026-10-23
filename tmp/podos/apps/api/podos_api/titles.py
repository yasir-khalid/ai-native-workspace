"""Short, human chat titles drawn from the first message.

The run engine keeps no LLM in the loop (see docs/DEVIATIONS.md #13), so titles are derived
deterministically: drop the conversational/imperative lead ("find the", "can you"), drop
stop-words, keep the first few content words, and preserve tickers/acronyms from the original.
The result is at most four words, so it never truncates in the sidebar.
"""

from __future__ import annotations

import re

# Words that carry no topic: greetings, question words, leading imperatives, and filler verbs.
_LEAD = {
    "hello", "hi", "hey", "please", "can", "could", "would", "will", "shall", "you", "i", "we",
    "find", "show", "tell", "give", "summarise", "summarize", "review", "chart", "make", "create",
    "draft", "explain", "compare", "analyse", "analyze", "list", "help", "look", "search", "pull",
    "get", "fetch", "run", "generate", "what", "whats", "what's", "is", "are", "do", "does", "did",
    "the", "a", "an", "our", "my", "your", "me", "us", "there", "how", "why", "when", "where",
}

_STOP = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "by", "as", "is", "are",
    "was", "were", "be", "been", "being", "with", "that", "this", "these", "those", "from", "it",
    "its", "our", "my", "your", "their", "we", "i", "you", "me", "us", "them", "do", "does", "did",
    "can", "could", "would", "should", "will", "shall", "may", "might", "must", "what", "which",
    "who", "whom", "whose", "when", "where", "why", "how", "about", "into", "over", "under", "so",
    "just", "also", "any", "all", "some", "more", "most", "much", "many", "no", "not", "only",
    "own", "same", "too", "very", "key", "top", "need", "want", "let", "ask", "give", "find",
    "show", "tell", "get", "summarise", "summarize", "review", "chart", "make", "create", "draft",
    "explain", "compare", "analyse", "analyze", "list", "help", "please", "there",
}

# Weak trailing words ("... ahead", "... next week") that read better dropped from the end.
_TRAIL = {
    "ahead", "next", "week", "soon", "again", "now", "later", "then", "first", "last", "today",
    "yesterday", "tomorrow", "here", "ok", "okay", "thanks", "thank", "please",
}

_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")


def short_title(message: str, max_words: int = 4) -> str:
    """A <=4-word title for a thread's first message (falls back to "New chat")."""
    raw = re.sub(r"[^a-z0-9'\u2019\-\s]", " ", message.lower()).split()
    if not raw:
        return "New chat"

    # Drop the conversational/imperative lead.
    i = 0
    while i < len(raw) and i < 8 and raw[i] in _LEAD:
        i += 1
    toks = raw[i:] or raw

    content = [t for t in toks if t not in _STOP]
    words = (content or toks)[:max_words]
    while len(words) > 1 and words[-1] in _TRAIL:
        words = words[:-1]

    acronyms = {a.upper() for a in _ACRONYM.findall(message)}
    out = []
    for w in words:
        if w.isalpha() and w.upper() in acronyms:
            out.append(w.upper())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out) or "New chat"

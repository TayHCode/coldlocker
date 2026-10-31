"""Offline search candidate. Not exposed by the production MCP endpoint."""
import re

SEARCH_QUERY_MAX = 500
SEARCH_LIMIT = 3
SEARCH_MIN_SCORE = 2
STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from get got had has have i if in into is it its me my "
    "of on or our please so than that the their then there these they this those to up us was we were what when where "
    "which who will with would you your".split())
CONCEPTS = {
    "robot": ("agent",), "bot": ("agent",), "ai": ("agent",), "assistant": ("agent",), "model": ("agent",),
    "remember": ("lesson", "encod"), "forget": ("lesson", "encod"), "mistak": ("lesson", "correction"),
    "error": ("failure",), "bug": ("failure",), "broken": ("failure",), "fail": ("failure",),
    "slow": ("performance",), "fast": ("performance",), "speed": ("performance",),
    "test": ("verif",), "check": ("verif",), "prove": ("verif", "evidence"), "proof": ("verif", "evidence"),
    "finish": ("done",), "complete": ("done",), "tri": ("prove", "verif"),
}


def stem(word):
    for suffix in ("ing", "ly", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            word = word[:-len(suffix)]
            break
    return word[:-1] if len(word) > 3 and word.endswith("e") else word


def terms(text):
    return {stem(word) for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOPWORDS}


def search_skills(catalog, query):
    normalized_concepts = {stem(key): values for key, values in CONCEPTS.items()}
    wanted = terms(query)
    for word in list(wanted):
        wanted.update(stem(related) for related in normalized_concepts.get(word, ()))
    scored = []
    for entry in catalog:
        score = (3 * len(wanted & terms(entry["name"].replace("-", " ")))
                 + len(wanted & terms(entry.get("description", ""))))
        if score >= SEARCH_MIN_SCORE:
            scored.append((score, entry))
    scored.sort(key=lambda row: (-row[0], row[1]["name"]))
    return {"results": [entry for _, entry in scored[:SEARCH_LIMIT]], "searched": len(catalog),
            "remaining": max(0, len(scored) - SEARCH_LIMIT)}



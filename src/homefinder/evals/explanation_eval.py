"""Phase 8.4: plain-code checks on the explanation text. No LLM, no network.

`check(text, facts)` returns a list of problems (empty = clean). `facts` is what `llm.explanation_facts` gave the model,
so the check sees exactly what the model saw. It tests faithfulness to the facts (numbers, names, order, flags, no
commute times, a verify reminder), not whether the advice is good: that needs a person or a judge model.

Known limits: names must appear in full (a shortened name counts as missing); a number the model invents that happens to
equal some other number in the facts (for example 12) passes; "12k" is read as 12.
"""
import re

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_LIST_MARK = re.compile(r"^\s*(?:[-*#>]+\s*)*\d+[.)]\s+", re.M)  # "1. " or "2) " at the start of a line is numbering, not a fact
_COMMUTE = re.compile(r"\b\d+\s*(?:-\s*\d+\s*)?(?:min|mins|minutes)\b|commute[^.\n]{0,40}\d", re.I)
_FLAG_WORDS = {"deposit estimated": ["estimat"], "re-verify": ["verif"]}  # a flag counts as mentioned if one of these appears


def _numbers(s: str) -> set[float]:
    return {round(float(m.replace(",", "")), 1) for m in _NUM.findall(s)}


def _facts_numbers(x) -> set[float]:
    """Every number that appears in the facts, as a value or inside a string (names, dates)."""
    if isinstance(x, bool) or x is None:
        return set()
    if isinstance(x, (int, float)):
        return {round(float(x), 1)}
    if isinstance(x, str):
        return _numbers(x)
    if isinstance(x, dict):
        return set().union(*(_facts_numbers(v) for v in x.values())) if x else set()
    return set().union(*(_facts_numbers(v) for v in x)) if x else set()


def _norm(s: str) -> str:
    """Lower case, runs of spaces collapsed; line breaks kept so an option's text can end at a blank line."""
    return re.sub(r"[ \t]+", " ", s).strip().lower()


def _flag_words(flag: str) -> list[str]:
    if flag in _FLAG_WORDS:
        return _FLAG_WORDS[flag]
    return ["first seen", "days"] if flag.startswith("first seen") else [flag.lower()]


def check(text: str, facts: dict) -> list[str]:
    problems = []
    body = _LIST_MARK.sub("", text)
    allowed = _facts_numbers(facts)
    bad = sorted(n for n in _numbers(body) if n not in allowed)
    if bad:
        problems.append("numbers not in the facts: " + ", ".join(f"{n:g}" for n in bad))

    flat = _norm(text)
    options = facts["options_in_rank_order"]
    spots = [(flat.find(_norm(o["name"])), o) for o in options]
    problems += [f"option not mentioned: {o['name']}" for i, o in spots if i < 0]
    found = [(i, o) for i, o in spots if i >= 0]
    if [o["name"] for _, o in sorted(found, key=lambda t: t[0])] != [o["name"] for _, o in found]:
        problems.append("options are not in the given rank order")

    ordered = sorted(found, key=lambda t: t[0])
    for k, (i, o) in enumerate(ordered):  # an option's text runs from its name to the next option's name or a blank line
        stop = flat.find("\n\n", i)
        end = min(ordered[k + 1][0] if k + 1 < len(ordered) else len(flat), stop if stop >= 0 else len(flat))
        seg = flat[i:end]
        for f in o["flags"]:
            if not any(w in seg for w in _flag_words(f)):
                problems.append(f"flag not mentioned for {o['name']}: {f}")

    if _COMMUTE.search(text):
        problems.append("mentions a commute time")
    last = [ln for ln in text.splitlines() if ln.strip()][-1:]  # the reminder is its own final line
    if not any("verif" in ln.lower() for ln in last):
        problems.append("no reminder to verify listings at the end")
    return problems

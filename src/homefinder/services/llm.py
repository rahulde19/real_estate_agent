import json
import re

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import ValidationError

from homefinder.agent.prompts import BATCH_RULES, EXTRACT_SYSTEM, explain_system
from homefinder.config.config import GEMINI_API_KEY, MODEL
from homefinder.core.rules import Decision
from homefinder.schemas import BatchAnswer, ListingBatch, UserProfile
from homefinder.services.metrics import METER


def _llm(json_mode: bool = False, max_tokens: int = 3000) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=MODEL,
        google_api_key=GEMINI_API_KEY,
        max_output_tokens=max_tokens,
        response_mime_type="application/json" if json_mode else None,
    )


def _invoke(llm, msgs, kind: str):
    """One LLM call, recorded in the run meter (calls and tokens)."""
    reply = llm.invoke(msgs)
    METER.add(kind, reply)
    return reply


class ExtractionInvalid(Exception):
    """The model's output was not valid listing JSON after a retry (recorded, not retried every run)."""


def _parse_batch(text: str) -> ListingBatch:
    m = re.search(r"\{.*\}", text, re.S)  
    return ListingBatch.model_validate_json(m.group(0) if m else text)


def single_system() -> str:
    schema = json.dumps(ListingBatch.model_json_schema())
    return f"{EXTRACT_SYSTEM}\n\nReply with ONLY a JSON object matching this JSON schema, no other text:\n{schema}"


def batch_system() -> str:
    schema = json.dumps(BatchAnswer.model_json_schema())
    return (f"{EXTRACT_SYSTEM}\n\n{BATCH_RULES}\n\n"
            f"Reply with ONLY a JSON object matching this JSON schema, no other text:\n{schema}")


def extract_document(title: str, url: str, content: str) -> ListingBatch:
    """One LLM call for one document: its listings, page type and evidence items.
    Independent of the user and city, so the result can be saved and reused."""
    msgs = [("system", single_system()), ("human", f"Title: {title}\nURL: {url}\n\n{content}")]

    llm = _llm()
    for attempt in range(2):
        reply = _invoke(llm, msgs, "extract").text
        try:
            answer = _parse_batch(reply)
        except (ValidationError, ValueError):
            msgs += [("assistant", reply), ("human", "That was not valid JSON for the schema")]
            continue
        for l in answer.listings:
            l.url = url  # provenance: the document we read, not what the model copied
            l.commute_minutes = None  # user-dependent; not extracted
        return answer
    raise ExtractionInvalid("The model did not return valid listing JSON.")


def batch_prompt(docs: list[tuple[int, str, str, str]]) -> str:
    return "\n\n".join(f"=== DOC {i} ===\nTitle: {title}\nURL: {url}\n{text}" for i, title, url, text in docs)


def extract_documents(docs: list[tuple[int, str, str, str]]) -> dict[int, ListingBatch]:
    """One LLM call for several documents: (doc_id, title, url, text) in, {doc_id: page answer} out.

    Single attempt, JSON output mode. Any invalid answer raises ExtractionInvalid so the caller can split the batch
    and retry smaller; the caller also checks that every document was answered. Source URLs come from our own
    doc_id mapping, never from the model.
    """
    urls = {i: url for i, _, url, _ in docs}
    llm = _llm(json_mode=True, max_tokens=min(8000, 1000 + 1000 * len(docs)))
    reply = _invoke(llm, [("system", batch_system()), ("human", batch_prompt(docs))], "extract").text
    try:
        m = re.search(r"\{.*\}", reply, re.S)
        answer = BatchAnswer.model_validate_json(m.group(0) if m else reply)
    except (ValidationError, ValueError) as e:
        raise ExtractionInvalid(f"invalid batch JSON: {type(e).__name__}") from e
    out: dict[int, ListingBatch] = {}
    for d in answer.documents:
        if d.doc_id in urls and d.doc_id not in out:  # unknown or repeated ids are ignored
            for l in d.listings:
                l.url = urls[d.doc_id]
                l.commute_minutes = None
            out[d.doc_id] = ListingBatch(source_type=d.source_type, listings=d.listings, evidence=d.evidence)
    return out


def explanation_facts(p: UserProfile, d: Decision) -> dict:
    """Exactly what the explanation model is given: the profile, the recommended kind and the ranked options."""
    options = [
        {
            "name": c.listing.name, "type": c.listing.kind, "area": c.listing.area,
            "monthly_cost": round(c.effective_monthly), "upfront_cash": round(c.upfront),
            "score_out_of_10": round(c.score, 1),
            "sub_scores": {k: round(v, 1) for k, v in c.scores.items()},
            "unknowns": c.listing.missing_fields,
            "confidence": c.confidence,
            "flags": c.flags,
        }
        for c in d.ranked
    ]
    return {"user": p.model_dump(mode="json"), "recommended_type": d.recommended_kind, "options_in_rank_order": options}


def write_suggestion(p: UserProfile, d: Decision) -> str:
    if not d.ranked:  # nothing ranked, nothing to explain: no LLM call
        return ""
    user = json.dumps(explanation_facts(p, d), indent=1)
    return _invoke(_llm(), [("system", explain_system(p.city)), ("human", user)], "explain").text

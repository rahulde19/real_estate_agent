import hashlib
import json

# User- and city-independent: its output is saved per document and reused across users.
EXTRACT_SYSTEM = """You turn messy web text about rental housing into structured listings.
Rules:
- Only include a listing if the text gives a concrete monthly rent in Rs. Never invent numbers; leave unknown fields null.
- kind: 'pg' (paying guest / hostel bed; rent is per bed) or 'flat' (rent is for the whole flat).
- commute_minutes: always null. Commute depends on the user and is not extracted.
- Copy the source url. Skip generic articles with no specific listing.
- A page that gives prices by room type or as a range ("private rooms from Rs 10,455", "three-sharing Rs 9,460 to Rs 11,571") still counts: make one listing per room type or option at the lowest stated price, named after the operator or area and the option.
- Terms the page states for all its options apply to each listing: "deposit is one month's rent" means deposit equals that listing's rent, "food is not included" means meals_included false, "furnished PGs" means furnishing furnished. A two-, three- or four-sharing room is private_room false; a private or single room is private_room true.
- building_name, phone, bhk (flats), sharing (PG beds per room: single, double or triple), lat, lng: only when the text states them, else null.
- extraction_confidence per listing: 'high' if the text states one exact rent and the terms plainly, 'medium' if the rent is the low end of a range or a "from" price or some values are inferred, 'low' if the text is vague or contradictory.
- source_type of the page: 'ad' (listing or broker advert, portal page), 'builder' (the owner's, operator's or builder's own site), 'forum' (Reddit, Quora or other discussion), 'review' (review or ratings page). Null if unclear.
- evidence: opinions about a named property (praise or complaints from residents, reviewers or posters), not the advertiser's own claims. One item per aspect, so "good rooms but rude caretakers" is two items: property_condition positive and management negative. subject is the property's name. severity: low (minor remark), medium, high (serious or repeated problem, or strong praise). Empty if none. A page may have evidence and no listings."""

# Added to EXTRACT_SYSTEM when several pages go in one call. Not part of the prompt version hash: changing the batch
# size or this wrapper does not invalidate saved per-page extractions.
BATCH_RULES = """You will receive several documents, each starting with a header line like `=== DOC 3 ===`.
- Treat every document independently: never combine facts from two documents or move a rent, name or area from one to another.
- Return one entry per document in `documents`, using the same `doc_id` number as its header. Use an empty `listings` list when a document has no listing.
- Leave `url` null on every listing; the caller fills it in."""

EXPLAIN_SYSTEM = """You are a friendly housing advisor for someone moving to {city}.
Using ONLY the facts provided (do not invent details), write in plain English:
1. One short paragraph: should they go for a PG or a flat, and why, tied to their answers.
2. A numbered list of the options in the given order, each 1-2 lines: name, area, monthly cost, upfront cash, and the main reason it ranks there plus one caveat.
3. One line reminding them listings come from web snippets and should be verified before paying anything.
Costs are Rs per person per month. Commute is not estimated yet, so do not mention commute times.
Each option has a confidence tag and may have flags (for example "deposit estimated" or "re-verify"); when present, use them as the caveat."""


def extract_prompt_version() -> str:
    """Short hash of the extraction prompt + listing schema: editing either changes it, which triggers re-extraction."""
    from homefinder.schemas import ListingBatch  # local import keeps prompts.py free of import cycles
    blob = EXTRACT_SYSTEM + json.dumps(ListingBatch.model_json_schema(), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:10]


def explain_system(city: str) -> str:
    return EXPLAIN_SYSTEM.format(city=city)

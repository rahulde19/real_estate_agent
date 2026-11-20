# Response 1 — search results used as LLM context

> **Historical record (written 08-11-2026 for an early run, not kept up to date).** The code has changed since: the cost formulas now live in `core/formulas.py` (`core/costs.py` is gone), extraction runs per page or in batches (`agent/extraction.py`, `services/llm.py::extract_document`), `commute_minutes` is no longer asked of the model, and the prompt text below is out of date. Read `docs/architecture.md` and `rulebook.md` for the current behaviour.

Source: `tavily_sample/tavily_ans.json` (default `SEARCH_SOURCE=sample`; no live Tavily call). Read by `search_sample()` in `services/search.py`, each `content` cut to 1,200 chars (none were longer). 6 results.

This is the text fed to **LLM #1 (extract)** in `extract_listings()` (`services/llm.py`). The LLM sees only this text and the work location. It never sees budget, cash or commute tolerance, so nothing is filtered at this stage.

## System prompt (`EXTRACT_SYSTEM`)

```
You turn messy web search results about rental housing into structured listings.
Rules:
- Only include a listing if the text gives a concrete monthly rent in Rs. Never invent numbers; leave unknown fields null.
- kind: 'pg' (paying guest / hostel bed; rent is per bed) or 'flat' (rent is for the whole flat).
- commute_minutes: your rough estimate of door-to-door travel time from the listing's area to the user's work location in Chennai. Null if the area is unknown.
- reputation_signals: one entry per distinct source mentioning this listing: +1 for positive reviews, -1 for complaints. Empty if none.
- Copy the source url. Skip generic articles with no specific listing.

Reply with ONLY a JSON object matching this JSON schema, no other text:
<ListingBatch JSON schema from schemas.py>
```

## Human message

```
Work location: <user's work location>, Chennai

Search results:
[0] pg in chennai
URL: https://zolostays.com/pg-in-chennai
Zolostays lists 74 PG properties in Chennai with rents from about Rs 4,720 to Rs 33,540 per month. Private rooms range from roughly Rs 10,455 to Rs 33,540, two-sharing from Rs 5,994 to Rs 16,922, and three-sharing from Rs 5,265 to Rs 14,362. Rent includes WiFi and housekeeping, electricity is billed on actuals, and the security deposit is one month's rent.

[1] pgs in velachery chennai
URL: https://zolostays.com/pgs-in-velachery-chennai
PG prices in Velachery span from under Rs 9,000 for shared rooms to about Rs 19,178 for a private room. Three-sharing is listed at Rs 9,460 to Rs 11,571, four-sharing at Rs 8,858 and two-sharing at Rs 11,610. Food is not included, and the deposit is one month's rent, refundable within 15 to 21 working days after move-out.

[2] chennai perungudi PG listings
URL: https://houssed.com/rent/pg/listing/chennai-perungudi
Furnished PG listings in Perungudi, Chennai include boys-only and co-ed options. Advertised rents range from about Rs 14,850 for a co-ed PG to Rs 30,100 for a boys-only PG. Figures are indicative averages from verified listings.

[3] 2 BHK Flat for Rent in Kodambakkam, Chennai
URL: https://housing.com/rent/18641576-1000-sqft-2-bhk-apartment-on-rent-in-kodambakkam-chennai
An unfurnished 1000 sq.ft 2 BHK in Kodambakkam is listed at Rs 20,000 per month, with lift and power backup, and is available from January 2026. Listing was last updated October 2025.

[4] 2 BHK Flat for Rent in Chetpet, Chennai
URL: https://housing.com/rent/19621284-1100-sqft-2-bhk-apartment-on-rent-in-chetpet-chennai
A fully furnished 1100 sq.ft 2 BHK in Chetpet is listed at Rs 40,000 per month with a security deposit of Rs 2,00,000. Price is marked as not negotiable. Listing was last updated March 2026.

[5] 2 BHK Flats for rent in Chennai | NoBroker
URL: https://www.nobroker.in/2bhk-flats-for-rent-in-chennai_chennai
NoBroker reports that typical 2 BHK rent in Chennai ranges from about Rs 3,000 to Rs 15,000 per month, depending on locality, furnishing and building category. The page cites July 2026 market data and notes that prices vary by society type, building age and amenities.
```

## What the LLM did with it

| # | Result | Listings extracted |
|---|--------|--------------------|
| 0 | Zolostays PG Chennai | 1 (rent Rs 4,720, the bottom of the range) |
| 1 | Zolostays PG Velachery | 1 (rent Rs 9,000, "under Rs 9,000") |
| 2 | Houssed Perungudi | 2 (co-ed Rs 14,850; boys-only Rs 30,100) |
| 3 | Housing.com Kodambakkam 2 BHK | 1 (Rs 20,000) |
| 4 | Housing.com Chetpet 2 BHK | 1 (Rs 40,000, deposit Rs 2,00,000) |
| 5 | NoBroker 2 BHK page | 0 (generic range, no specific listing) |

6 listings from 5 usable results (the 6th result, NoBroker, is a generic range). This is the ceiling on how many options can reach scoring. Rents for rows 0-2 were inferred from the app's output (see `app_recc.md`), not read from the saved LLM reply.

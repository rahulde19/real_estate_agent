# Key review

Confirmed by the user in chat on 20-11-2026, all eight decisions as written. The 16 pages named here have `reviewed: user` in `pages.json`; the other 26 stay `reviewed: claude` (exact prices or clear junk, never looked at by a person). No key content changed.

| Pages | Question | Decision |
|---|---|---|
| `g01`, `g02` (Zolostays), `g07` (Stanza), `g08` (MagicBricks) | Is a range or "from" price a listing? | **Yes**: one listing per option at the low end, confidence medium. |
| `g16` MagicBricks house, "₹1.7 Lac", "Security deposit 10 month" | Listing? Deposit? | **Listing at rent 170,000, deposit left empty on purpose**: the page summarises 9 houses with one price, so a 10 x rent deposit would rest on an aggregate. (An earlier version of this file wrongly said the key had a 10-month deposit.) |
| `g06` (NoBroker market range), `g22` (Stanza, text cut off), `g23` (Quora title) | No listing? | **No listing.** `g23` stays `source_type: forum`. |
| `g17` 99acres, two flats | Two listings? | **Yes**, ₹13,001 and ₹10,000, each with its deposit. |
| `g25`, `g26`, `g27`, `g28` Yelp hostels (New York, Seattle, Austin, Honolulu) | Evidence expected? | **No.** Off-topic: evidence items returned for them count as errors, and `source_type` is not scored. |
| `s13` Kothrud PG, "Rs 7,000 ... Updated: now Rs 12,000" | Which price? | **12,000**, with 7,000 also accepted; expected confidence "low". |
| `s07` Palm Residency review | Items and severity? | **Two items**: property_condition positive, management negative; both medium. |
| `s09` Lotus Court forum complaint | One item or two? | **One item**: deposit_refund negative, high. |

Ids: `g01`–`g06` are the saved sample, `g07`–`g28` the live capture, `s01`–`s14` synthetic.

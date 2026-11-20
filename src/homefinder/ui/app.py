"""Streamlit intake form + results. Run: streamlit run src/homefinder/ui/app.py"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from homefinder.agent.graph import build_graph
from homefinder.config.config import RUN_LOG_PATH, SEARCH_SOURCE, load_rulebook, require_keys, sample_supports
from homefinder.core.evidence import caveats_by_criterion
from homefinder.schemas import UserProfile
from homefinder.services.search import sample_mode_message

st.set_page_config(page_title="Home Finder", page_icon="🏠")
st.title("🏠 Home Finder")
st.caption("Tell us about your move. We'll search the web and suggest your top 7 PGs / flats in your city.")
if SEARCH_SOURCE != "live":
    st.caption("Sample search mode: saved data covers Chennai only. Set SEARCH_SOURCE=live for other cities.")

if missing := require_keys():
    st.error(f"Missing in .env: {', '.join(missing)}")
    st.stop()

MUST = "Must-have"
MISS = {"budget": "Monthly budget", "upfront": "Upfront cash", "commute": "Commute", "must_have": "Must-have"}


def row(i, c):
    """One table row for a ranked option or a closest miss."""
    l = c.listing
    flags = c.flags + ([f"{len(l.observations)} sources"] if len(l.observations) > 1 else [])
    return {
        "#": i, "Name": l.name, "Type": l.kind.upper(), "Area": l.area,
        "₹/month": round(c.effective_monthly), "Upfront ₹": round(c.upfront),
        "vs city rent": None if c.rent_vs_benchmark_pct is None else f"{c.rent_vs_benchmark_pct:+.0f}%",
        "Score /10": round(c.score, 1), "Confidence": c.confidence, "Flags": ", ".join(flags), "Link": l.url,
    }


def details(c, aspect_criteria):
    """Where an option's facts came from, what is unknown, and what reviewers said."""
    l = c.listing
    st.write(f"**Confidence: {c.confidence}**" + (f" ({'; '.join(c.confidence_reasons)})" if c.confidence_reasons else ""))
    st.write(f"Deposit: {c.deposit_basis}."
             + (f" First seen {c.age_days} days ago, score reduced by {c.staleness_penalty:g}." if c.staleness_penalty else "")
             + (" **Re-verify that it is still available.**" if c.reverify else ""))
    for o in l.observations:
        st.write(f"- Source: {o.source_url} ({o.source_type or 'type unknown'}, via {o.agent_source}; "
                 f"first seen {o.first_seen_at[:10]}, last fetched {o.retrieved_at[:10]})")
    for criterion, lines in caveats_by_criterion(l.evidence, aspect_criteria).items():
        st.write(f"- What others say ({criterion}): " + "; ".join(lines))

with st.form("intake"):
    city = st.text_input("City you are moving to", "Chennai")  # "Chennai" is the dev default
    work = st.text_input("Work location (area or address)", placeholder="e.g. Tidel Park, Taramani")
    c1, c2 = st.columns(2)
    budget = c1.number_input("Monthly budget ceiling (₹)", min_value=1000, value=15000, step=500)
    cash = c2.number_input("Upfront cash available (₹)", min_value=1000, value=60000, step=5000)
    c3, c4 = st.columns(2)
    horizon = c3.selectbox("Stay horizon", ["6 months", "12 months", "24 months", "Not sure"], index=1)
    tolerance = c4.selectbox("Commute tolerance (not used until commute estimates return)", ["20 min", "40 min", "60 min", "90+ min"], index=1)
    c5, c6 = st.columns(2)
    mover = c5.selectbox("Who is moving", ["solo", "friends", "family"])
    size = c6.number_input("Total people (incl. you; ignored if solo)", min_value=1, value=1)
    move_in = st.date_input("Move-in date", date.today() + timedelta(days=14))

    st.markdown("**Lifestyle**")
    rows = [
        ("food", "Food", ["cook myself", "need meals", "eat out"]),
        ("privacy", "Privacy", ["private room", "sharing is fine"]),
        ("furnishing", "Furnishing", ["furnished", "semi", "unfurnished"]),
        ("noise", "Noise and social life", ["quiet", "social", "no preference"]),
        ("gender", "Gender-restricted housing", ["not needed", "needed"]),
    ]
    answers, must = {}, {}
    for key, label, opts in rows:
        a, b = st.columns([3, 1])
        answers[key] = a.radio(label, opts, horizontal=True)
        must[key] = b.checkbox(MUST, key=f"must_{key}")
    go = st.form_submit_button("Find my home", type="primary")

if go:
    if not city.strip() or not work.strip():
        st.warning("Please enter your city and work location.")
        st.stop()
    if SEARCH_SOURCE != "live" and not sample_supports(city):
        st.error(sample_mode_message(city.strip()))
        st.stop()
    n = 1 if mover == "solo" else max(2 if mover == "friends" else 1, int(size))
    profile = UserProfile(
        city=city.strip(), work_location=work.strip(), budget=budget, upfront_cash=cash,
        horizon_months=None if horizon == "Not sure" else int(horizon.split()[0]),
        mover=mover, group_size=n, commute_tolerance=int(tolerance.split()[0]),
        move_in_date=move_in, food=answers["food"], privacy=answers["privacy"],
        furnishing=answers["furnishing"], noise=answers["noise"],
        gender_restricted=answers["gender"] == "needed", must_have=must,
    )
    with st.spinner("Searching, reading listings and scoring… (about a minute)"):
        try:
            state = build_graph(run_log=RUN_LOG_PATH).invoke({"profile": profile})
        except Exception as e:  # surface API/network problems to the user
            st.error(f"Something went wrong: {e}")
            st.stop()

    d = state["decision"]
    stats = state.get("extraction_stats", {})
    m = state.get("run_metrics", {})
    tin, tout = sum(m.get("tokens_in", {}).values()), sum(m.get("tokens_out", {}).values())
    sec = m.get("seconds", {})
    st.caption(
        f"Pages: {m.get('pages', 0)} found, {stats.get('skipped', 0)} skipped by the filter, {stats.get('reused', 0)} reused "
        f"from saved extractions, {stats.get('extracted', 0)} newly extracted, {stats.get('failed', 0)} unreadable. "
        f"Extraction calls: {stats.get('calls', 0)}; LLM tokens in/out (extract + explain): {tin:,}/{tout:,}; "
        f"time: search {sec.get('search', 0)}s, extract {sec.get('extract', 0)}s, explain {sec.get('explain', 0)}s.")
    aspects = load_rulebook()["evidence"]["aspect_criteria"]
    if stats.get("duplicates_merged"):
        st.caption(f"{stats['duplicates_merged']} repeated ads were merged into the properties below.")
    st.subheader("Our suggestion")
    if not d.ranked:
        st.warning(f"No listings passed your filters ({len(state['listings'])} found, "
                   f"{len(d.rejected)} rejected). Try a higher budget, longer commute or fewer must-haves."
                   + (" The closest misses are shown below." if d.closest_misses else ""))
    else:
        st.markdown(state["suggestion"])
        st.caption("Commute is not estimated yet: its 20% weight gives every option the same score, so it does not change the ranking.")
        st.subheader(f"Top {len(d.ranked)} options")
        st.dataframe(
            pd.DataFrame([row(i, c) for i, c in enumerate(d.ranked, 1)]),
            hide_index=True, column_config={"Link": st.column_config.LinkColumn()},
        )
        for i, c in enumerate(d.ranked, 1):
            with st.expander(f"{i}. {c.listing.name}: sources, confidence and caveats"):
                details(c, aspects)
        basis = d.ranked[0].cost_basis
        st.caption({"city data": f"Food and utilities estimated from {profile.city} cost-of-living data.",
                    "city data (suspect)": f"Food and utilities from {profile.city} cost-of-living data, which looks unreliable.",
                    }.get(basis, f"No usable cost data for {profile.city}: food and utilities use default assumptions.")
                   + " 'vs city rent' compares your per-person rent with the city's one-person rent (not scored).")
    if d.closest_misses:
        st.subheader(f"Closest misses ({len(d.closest_misses)})")
        st.caption(f"Fewer than {load_rulebook()['decision']['top_n']} listings passed every hard limit. "
                   "These failed exactly one, nearest first. They are not recommendations.")
        st.dataframe(
            pd.DataFrame([{
                **row(i, c),
                "Failed": MISS[c.rejections[0].kind],
                "By": "" if c.rejections[0].gap is None else f"{c.rejections[0].gap:.0%}",
                "Detail": c.rejections[0].text,
            } for i, c in enumerate(d.closest_misses, 1)]),
            hide_index=True, column_config={"Link": st.column_config.LinkColumn()},
            column_order=["#", "Name", "Type", "Area", "Failed", "By", "Detail", "₹/month", "Upfront ₹", "Confidence", "Link"],
        )
    if d.rejected:
        with st.expander(f"Rejected ({len(d.rejected)})"):
            for c in d.rejected:
                st.write(f"**{c.listing.name}** — {'; '.join(c.reasons_rejected)}")

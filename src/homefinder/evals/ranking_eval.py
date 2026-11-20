"""Phase 8.3: run the whole pipeline on the golden set with the answer keys as the extractor, and snapshot the result.
No LLM, no network. The snapshot guards against unintended changes; it is not an independent answer key."""
from homefinder.agent.graph import build_graph
from homefinder.config.config import load_rulebook
from homefinder.evals.golden import as_results, load_pages, load_profiles, stub_extractor
from homefinder.schemas import UserProfile
from homefinder.services.documents import DocumentRepo


def plain_rulebook(**decision) -> dict:
    """One call per page, no prefilter (the stub extractor needs none); `decision` overrides the [decision] table."""
    rb = load_rulebook()
    return {**rb, "extraction": {**rb["extraction"], "prefilter": False, "batch_size": 1},
            "decision": {**rb["decision"], **decision}}


def run(profile: UserProfile, pages: list[dict] | None = None, rulebook: dict | None = None):
    """The graph's final state for one profile on the golden pages of its city (cost data is the labelled defaults)."""
    pages = [p for p in (pages if pages is not None else load_pages()) if p["city"].lower() == profile.city.lower()]
    g = build_graph(search=lambda loc, city: as_results(pages), extract=stub_extractor(pages), explain=lambda p, d: "ok",
                    repo=DocumentRepo(":memory:"), prompt_version="v1", model_id="golden", rulebook=rulebook or plain_rulebook(),
                    city_lookup=lambda c: None, agent_source="golden")
    return g.invoke({"profile": profile})


def snapshot(out: dict) -> dict:
    """Ranked names with scores, the recommended kind, and the closest misses with the cutoff each one failed."""
    d = out["decision"]
    return {"ranked": [{"name": c.listing.name, "score": round(c.score, 2)} for c in d.ranked],
            "recommended_kind": d.recommended_kind,
            "closest_misses": [{"name": c.listing.name, "failed": c.rejections[0].kind} for c in d.closest_misses]}


def snapshot_all() -> dict:
    return {pid: snapshot(run(p)) for pid, p in load_profiles().items()}

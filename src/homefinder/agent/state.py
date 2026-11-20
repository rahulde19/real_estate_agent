from typing import TypedDict

from homefinder.core.rules import Candidate, Decision
from homefinder.schemas import EvidenceItem, Listing, UserProfile


class AgentState(TypedDict, total=False):
    profile: UserProfile
    search_results: list[dict]
    listings: list[Listing]  # one per property (duplicates merged), with provenance and matched evidence
    evidence: list[EvidenceItem]  # every evidence item extracted this run
    extraction_stats: dict  # reused / extracted / failed / skipped documents, extraction calls
    run_metrics: dict  # calls, tokens, seconds per step (Phase 6.1)
    candidates: list[Candidate]
    decision: Decision
    suggestion: str

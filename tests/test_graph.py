from conftest import pg
from homefinder.agent.graph import build_graph
from conftest import plain_rulebook
from homefinder.services.documents import DocumentRepo


def test_graph_end_to_end_with_stubs(profile):
    seen = {}
    g = build_graph(
        search=lambda loc, city: [{"title": "t", "url": "u", "content": "c"}],
        extract=lambda title, url, text: [pg(name="A", private_room=True), pg(name="A", private_room=True), pg(name="B", rent=99999)],
        repo=DocumentRepo(":memory:"), prompt_version="v1", model_id="m", rulebook=plain_rulebook(),
        city_lookup=lambda city: None,  # wiring test: keep it independent of the cost dataset
        explain=lambda p, d: seen.setdefault("top", [c.listing.name for c in d.ranked]) and "ok",
    )
    out = g.invoke({"profile": profile})
    assert len(out["listings"]) == 2  # duplicate A removed
    assert seen["top"] == ["A"]
    assert out["suggestion"] == "ok"

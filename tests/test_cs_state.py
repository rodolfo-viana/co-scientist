import json
from pathlib import Path

import pytest

import cs_state


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")


def add_hyp(run: Path, hid: str, origin: str = "generated", rnd: int = 1, citations=None) -> None:
    write(
        run / "hypotheses" / f"{hid}.json",
        {
            "id": hid,
            "title": f"Title {hid}",
            "summary": "s",
            "origin": origin,
            "round": rnd,
            "parents": [],
            "citations": citations or [],
        },
    )
    write(run / "hypotheses" / f"{hid}.md", f"# {hid}\n")


def add_reviews(run: Path, hid: str, passed: bool = True, verdict: str = "accept") -> None:
    write(run / "reviews" / f"{hid}.initial.json", {"id": hid, "pass": passed, "reason": "r"})
    if passed:
        write(
            run / "reviews" / f"{hid}.json",
            {"id": hid, "verdict": verdict, "scores": {"novelty": 3, "plausibility": 4, "testability": 5}},
        )


def add_game(run: Path, mid: str, order: str, first: str, second: str, winner: str, rnd: int = 1) -> None:
    write(
        run / "matches" / f"{mid}-{order}.json",
        {"match": mid, "round": rnd, "order": order, "first": first, "second": second, "winner": winner},
    )


@pytest.fixture
def run(tmp_path: Path) -> Path:
    assert cs_state.main(["init", "--run-dir", str(tmp_path)]) == 0
    return tmp_path


def test_elo_update_is_zero_sum_and_symmetric():
    a, b = cs_state.elo_update(1200, 1200, 1.0, 32)
    assert a == pytest.approx(1216) and b == pytest.approx(1184)
    a2, b2 = cs_state.elo_update(1300, 1100, 0.5, 32)
    assert a2 + b2 == pytest.approx(2400)
    assert a2 < 1300  # a draw against a weaker player loses rating


def test_init_writes_default_config_once(run: Path):
    cfg = json.loads((run / "config.json").read_text())
    assert cfg["ideate"]["elo_init"] == 1200
    cfg["ideate"]["elo_k"] = 16
    (run / "config.json").write_text(json.dumps(cfg))
    cs_state.main(["init", "--run-dir", str(run)])
    assert json.loads((run / "config.json").read_text())["ideate"]["elo_k"] == 16


def test_rebuild_statuses_ratings_and_position_disagreement(run: Path):
    for hid in ("H001", "H002", "H003", "H004", "H005"):
        add_hyp(run, hid)
    add_reviews(run, "H001")
    add_reviews(run, "H002", verdict="revise")
    add_reviews(run, "H003", passed=False)
    add_reviews(run, "H004", verdict="reject")
    add_reviews(run, "H005")
    write(run / "rounds" / "R01" / "dedup.json", {"round": 1, "clusters": [{"keep": "H001", "drop": ["H005"]}]})
    # M0001: H001 wins both orders. M0002: positions disagree -> draw.
    add_game(run, "M0001", "ab", "H001", "H002", "H001")
    add_game(run, "M0001", "ba", "H002", "H001", "H001")
    add_game(run, "M0002", "ab", "H002", "H001", "H002")
    add_game(run, "M0002", "ba", "H001", "H002", "H001")

    state = cs_state.build_state(run)
    status = {h["id"]: h["status"] for h in state["hypotheses"]}
    assert status == {
        "H001": "active",
        "H002": "active",
        "H003": "rejected-initial",
        "H004": "rejected",
        "H005": "duplicate",
    }
    r1, r2 = cs_state.elo_update(1200, 1200, 1.0, 32)
    r2b, r1b = cs_state.elo_update(r2, r1, 0.5, 32)
    assert state["ratings"]["H001"] == pytest.approx(r1b, abs=1e-6)
    assert state["ratings"]["H002"] == pytest.approx(r2b, abs=1e-6)
    assert state["position_disagreements"] == 1
    assert state["next_match"] == 3 and state["next_hypothesis"] == 6 and state["next_round"] == 2
    assert state["problems"] == []


def test_rebuild_flags_bad_games_and_rating_mismatch(run: Path, capsys):
    add_hyp(run, "H001")
    add_hyp(run, "H002")
    add_reviews(run, "H001")
    add_reviews(run, "H002")
    add_game(run, "M0001", "ab", "H001", "H002", "H009")  # winner not in pair
    add_game(run, "M0002", "ab", "H001", "H002", "H001")
    write(run / "expect.json", {"H001": 1250.0})
    code = cs_state.main(["rebuild", "--run-dir", str(run), "--expect-ratings", str(run / "expect.json")])
    out = capsys.readouterr().out
    assert code == 1
    assert "winner must be" in out and "rating mismatch for H001" in out
    assert (run / "ranking.md").exists() and (run / "state.json").exists()


def test_test_verdict_overrides_review_status(run: Path):
    add_hyp(run, "H001")
    add_reviews(run, "H001")
    write(run / "tests" / "H001" / "plan.json", {"id": "H001"})
    state = cs_state.build_state(run)
    assert state["hypotheses"][0]["status"] == "test-planned"
    write(run / "tests" / "H001" / "verdict.json", {"id": "H001", "verdict": "refuted"})
    state = cs_state.build_state(run)
    assert state["hypotheses"][0]["status"] == "refuted"


def test_rebuild_writes_audit_file_and_tests_section(run: Path):
    add_hyp(run, "H001")
    add_reviews(run, "H001")
    write(
        run / "tests" / "H001" / "verdict.json",
        {
            "id": "H001",
            "verdict": "inconclusive",
            "rule_decision": "inconclusive",
            "decided_by": "rule",
            "audit": {"verdict_holds": True, "issues": ["title overclaims", "seed arithmetic is wrong"]},
        },
    )
    assert cs_state.main(["rebuild", "--run-dir", str(run)]) == 0
    audit = (run / "tests" / "H001" / "audit.md").read_text()
    assert "1. title overclaims" in audit and "2. seed arithmetic is wrong" in audit
    ranking = (run / "ranking.md").read_text()
    assert "## Tests" in ranking and "| H001 | inconclusive | rule | inconclusive | True | 2 |" in ranking


def test_summary_lists_unreviewed_user_hypotheses(run: Path, capsys):
    add_hyp(run, "H001")
    add_reviews(run, "H001")
    add_hyp(run, "H002", origin="user", rnd=5)
    cs_state.main(["summary", "--run-dir", str(run), "--skill-dir", str(run)])
    payload = json.loads(capsys.readouterr().out)
    prior = payload["prior"]
    assert prior["round"] == 2  # user hypotheses do not advance the round counter
    assert [u["id"] for u in prior["unreviewed"]] == ["H002"]
    assert prior["ratings"] == {"H001": 1200.0}
    assert prior["nextHypothesis"] == 3
    assert payload["config"]["models"]["judge"]["model"] == "opus"


def test_citation_snippets_are_checked_against_stored_abstracts(run: Path):
    write(
        run / "literature" / "arxiv-1234.5678.json",
        {"key": "arxiv-1234.5678", "title": "A paper", "abstract": "We show that  Wide Networks generalize."},
    )
    cites = [
        {"key": "arxiv-1234.5678", "claim": "c", "snippet": "We show that  Wide Networks generalize."},
        {"key": "arxiv-1234.5678", "claim": "c", "snippet": "we show that wide networks generalize"},
        {"key": "arxiv-1234.5678", "claim": "c", "snippet": "deep networks memorize"},
        {"key": "missing-key", "claim": "c", "snippet": "x"},
        {"key": "../etc/passwd", "claim": "c", "snippet": "x"},
    ]
    add_hyp(run, "H001", citations=cites)
    state = cs_state.build_state(run)
    statuses = [c["status"] for c in state["citations"]]
    assert statuses == ["verified", "verified-normalized", "mismatch", "missing-record", "bad-key"]


def test_online_check_uses_openalex_and_caches(run: Path, monkeypatch):
    payload = {
        "title": "A paper",
        "abstract_inverted_index": {"We": [0], "show": [1], "things": [2]},
    }
    calls = []

    def fake_fetch(url, timeout=15.0):
        calls.append(url)
        return json.dumps(payload).encode()

    monkeypatch.setattr(cs_state, "_fetch", fake_fetch)
    write(
        run / "literature" / "openalex-W1.json",
        {"key": "openalex-W1", "title": "A paper", "abstract": "We show things", "ids": {"openalex": "W1"}},
    )
    add_hyp(run, "H001", citations=[{"key": "openalex-W1", "claim": "c", "snippet": "We show things"}])
    state = cs_state.build_state(run, online=True)
    assert state["citations"][0]["online"] == "verified"
    cs_state.build_state(run, online=True)
    assert len(calls) == 1  # second run reads the cache
    assert calls[0].endswith("/works/W1")

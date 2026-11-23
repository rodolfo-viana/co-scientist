#!/usr/bin/env python3
"""Run-state tool for the co-scientist skill.

The run directory is the single source of truth. Agents write small JSON
sidecars next to their markdown output; this script validates them,
recomputes Elo ratings from the match files and writes ``state.json`` and
``ranking.md``. It uses only the Python standard library.

Usage:
    cs_state.py init --run-dir DIR [--override FILE]
    cs_state.py summary --run-dir DIR --skill-dir DIR
    cs_state.py rebuild --run-dir DIR [--expect-ratings FILE] [--online]
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

_REASONING = {"model": "opus", "effort": "xhigh"}
_CODE = {"model": "sonnet", "effort": "xhigh"}

DEFAULT_CONFIG: dict[str, Any] = {
    "language": "en",
    "ideate": {
        "n_generate": 6,
        "swiss_rounds": 3,
        "n_evolve": 3,
        "evolved_swiss_rounds": 2,
        "top_k": 4,
        "elo_k": 32,
        "elo_init": 1200,
    },
    "tests": {
        "smoke_max_minutes": 5,
        "local_max_minutes_per_hypothesis": 30,
        "command_max_minutes": 9,
        "default_seeds": 5,
    },
    "models": {
        "generator": dict(_REASONING),
        "reviewer_initial": dict(_REASONING),
        "reviewer": dict(_REASONING),
        "deduplicator": dict(_REASONING),
        "judge": dict(_REASONING),
        "evolver": dict(_REASONING),
        "meta_reviewer": dict(_REASONING),
        "test_designer": dict(_REASONING),
        "implementer": dict(_CODE),
        "runner": dict(_CODE),
        "analyst": dict(_REASONING),
        "finalizer": {"model": "sonnet", "effort": "medium"},
    },
    "wandb": {"enabled": True, "project": "co-scientist", "entity": None},
}

SUBDIRS = ("hypotheses", "reviews", "matches", "literature", "rounds", "tests")
HYP_ID = re.compile(r"^H(\d{3,})$")
GAME_FILE = re.compile(r"^(M(\d{4,}))-(ab|ba)\.json$")
LIT_KEY = re.compile(r"^[A-Za-z0-9._-]+$")
VERDICTS = ("supported", "refuted", "inconclusive")
USER_AGENT = "co-scientist-skill/0.1"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` updated recursively with ``override``."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def hyp_number(hyp_id: str) -> int:
    match = HYP_ID.match(hyp_id)
    return int(match.group(1)) if match else 0


def pair_key(a: str, b: str) -> str:
    return f"{a}|{b}" if a < b else f"{b}|{a}"


def normalize_text(text: str) -> str:
    text = text.lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(".,;:!?\"'()[]{} ")


def elo_expected(r_a: float, r_b: float) -> float:
    return 1.0 / (1.0 + math.pow(10.0, (r_b - r_a) / 400.0))


def elo_update(r_a: float, r_b: float, score_a: float, k: float) -> tuple[float, float]:
    """Standard Elo update; must stay identical to ``applyElo`` in ideate.js."""
    e_a = elo_expected(r_a, r_b)
    return r_a + k * (score_a - e_a), r_b + k * ((1.0 - score_a) - (1.0 - e_a))


class Problems:
    """Collects validation problems instead of failing on the first one."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, message: str) -> None:
        self.items.append(message)


def _read(path: Path, problems: Problems) -> Any | None:
    try:
        return load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        problems.add(f"{path.name}: unreadable JSON ({exc})")
        return None


# --------------------------------------------------------------------------
# Loading the run directory
# --------------------------------------------------------------------------


def load_config(run: Path) -> dict[str, Any]:
    path = run / "config.json"
    if path.exists():
        return deep_merge(DEFAULT_CONFIG, load_json(path))
    return copy.deepcopy(DEFAULT_CONFIG)


def load_hypotheses(run: Path, problems: Problems) -> dict[str, dict[str, Any]]:
    hyps: dict[str, dict[str, Any]] = {}
    for path in sorted((run / "hypotheses").glob("H*.json")):
        data = _read(path, problems)
        if not isinstance(data, dict):
            continue
        hyp_id = path.stem
        if data.get("id") != hyp_id:
            problems.add(f"{path.name}: id field {data.get('id')!r} does not match file name")
            data["id"] = hyp_id
        for field in ("title", "summary", "origin"):
            if not data.get(field):
                problems.add(f"{path.name}: missing field {field!r}")
        if not (run / "hypotheses" / f"{hyp_id}.md").exists():
            problems.add(f"{hyp_id}: hypothesis markdown file is missing")
        hyps[hyp_id] = data
    return hyps


def load_reviews(
    run: Path, problems: Problems
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    initial: dict[str, dict[str, Any]] = {}
    full: dict[str, dict[str, Any]] = {}
    for path in sorted((run / "reviews").glob("H*.json")):
        data = _read(path, problems)
        if not isinstance(data, dict):
            continue
        if path.name.endswith(".initial.json"):
            hyp_id = path.name[: -len(".initial.json")]
            if not isinstance(data.get("pass"), bool):
                problems.add(f"{path.name}: 'pass' must be true or false")
                continue
            initial[hyp_id] = data
        else:
            hyp_id = path.stem
            if data.get("verdict") not in ("accept", "revise", "reject"):
                problems.add(f"{path.name}: verdict must be accept, revise or reject")
                continue
            full[hyp_id] = data
    return initial, full


def load_duplicates(run: Path, problems: Problems) -> dict[str, str]:
    dropped: dict[str, str] = {}
    for path in sorted((run / "rounds").glob("R*/dedup*.json")):
        data = _read(path, problems)
        if not isinstance(data, dict):
            continue
        for cluster in data.get("clusters", []):
            keep = cluster.get("keep")
            for drop in cluster.get("drop", []):
                if drop != keep:
                    dropped[drop] = keep
    return dropped


def load_games(run: Path, problems: Problems) -> dict[str, dict[str, dict[str, Any]]]:
    games: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted((run / "matches").glob("M*.json")):
        match = GAME_FILE.match(path.name)
        if not match:
            problems.add(f"{path.name}: unexpected file name in matches/")
            continue
        data = _read(path, problems)
        if not isinstance(data, dict):
            continue
        match_id, order = match.group(1), match.group(3)
        first, second, winner = data.get("first"), data.get("second"), data.get("winner")
        if not first or not second or winner not in (first, second):
            problems.add(f"{path.name}: winner must be the first or second hypothesis")
            continue
        games.setdefault(match_id, {})[order] = data
    return games


def build_matches(
    games: dict[str, dict[str, dict[str, Any]]], problems: Problems
) -> list[dict[str, Any]]:
    """Combine the two position-swapped games of each match into one score."""
    matches: list[dict[str, Any]] = []
    for match_id in sorted(games, key=lambda m: int(m[1:])):
        pair = games[match_id]
        ab, ba = pair.get("ab"), pair.get("ba")
        if ab and ba and (ab["first"], ab["second"]) != (ba["second"], ba["first"]):
            problems.add(f"{match_id}: the ab and ba games do not swap the same pair")
            continue
        if ab:
            a, b = ab["first"], ab["second"]
        else:
            assert ba is not None
            a, b = ba["second"], ba["first"]
        winners = [g["winner"] for g in (ab, ba) if g]
        score_a = sum(1.0 for w in winners if w == a) / len(winners)
        round_no = (ab or ba or {}).get("round")
        matches.append(
            {
                "id": match_id,
                "round": round_no,
                "a": a,
                "b": b,
                "score_a": score_a,
                "games": len(winners),
                "position_disagreement": len(winners) == 2 and winners[0] != winners[1],
            }
        )
    return matches


def compute_ratings(
    matches: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[dict[str, float], dict[str, list[int]]]:
    k = float(config["ideate"]["elo_k"])
    init = float(config["ideate"]["elo_init"])
    ratings: dict[str, float] = {}
    record: dict[str, list[int]] = {}  # wins, draws, losses
    for m in matches:
        a, b, s = m["a"], m["b"], m["score_a"]
        ratings.setdefault(a, init)
        ratings.setdefault(b, init)
        ratings[a], ratings[b] = elo_update(ratings[a], ratings[b], s, k)
        rec_a = record.setdefault(a, [0, 0, 0])
        rec_b = record.setdefault(b, [0, 0, 0])
        if s > 0.5:
            rec_a[0] += 1
            rec_b[2] += 1
        elif s < 0.5:
            rec_a[2] += 1
            rec_b[0] += 1
        else:
            rec_a[1] += 1
            rec_b[1] += 1
    return ratings, record


def load_tests(run: Path, problems: Problems) -> dict[str, dict[str, Any]]:
    tests: dict[str, dict[str, Any]] = {}
    for test_dir in sorted((run / "tests").glob("H*")):
        if not test_dir.is_dir():
            continue
        entry: dict[str, Any] = {}
        for name in ("plan", "prepare", "run", "stats", "verdict"):
            path = test_dir / f"{name}.json"
            if path.exists():
                data = _read(path, problems)
                if data is not None:
                    entry[name] = data
        verdict = entry.get("verdict")
        if verdict is not None and verdict.get("verdict") not in VERDICTS:
            problems.add(f"tests/{test_dir.name}/verdict.json: verdict must be one of {VERDICTS}")
            entry.pop("verdict")
        tests[test_dir.name] = entry
    return tests


def status_of(
    hyp_id: str,
    initial: dict[str, dict[str, Any]],
    full: dict[str, dict[str, Any]],
    duplicates: dict[str, str],
    tests: dict[str, dict[str, Any]],
) -> str:
    test = tests.get(hyp_id, {})
    if "verdict" in test:
        return str(test["verdict"]["verdict"])
    if "run" in test:
        return "tested-awaiting-analysis"
    if "plan" in test:
        return "test-planned"
    if hyp_id in duplicates:
        return "duplicate"
    if hyp_id in initial and not initial[hyp_id]["pass"]:
        return "rejected-initial"
    if hyp_id in full:
        return "rejected" if full[hyp_id]["verdict"] == "reject" else "active"
    return "unreviewed"


# --------------------------------------------------------------------------
# Citation checks (adapted from OpenScientist's snippet validation)
# --------------------------------------------------------------------------


def _fetch(url: str, timeout: float = 15.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _get(url: str, attempts: int = 3, backoff: float = 2.0) -> bytes:
    """Call _fetch, retrying rate limits, server errors and timeouts."""
    for attempt in range(attempts):
        try:
            return _fetch(url)
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt == attempts - 1:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == attempts - 1:
                raise
        time.sleep(backoff * (attempt + 1))
    raise RuntimeError("unreachable")


def _openalex_record(ids: dict[str, Any]) -> tuple[str, str] | None:
    if ids.get("openalex"):
        work = str(ids["openalex"]).rstrip("/").rsplit("/", 1)[-1]
        url = f"https://api.openalex.org/works/{urllib.parse.quote(work)}"
    elif ids.get("doi"):
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(ids["doi"]))
        url = f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='/')}"
    else:
        return None
    data = json.loads(_get(url))
    inverted = data.get("abstract_inverted_index") or {}
    positions = sorted((pos, word) for word, poss in inverted.items() for pos in poss)
    abstract = " ".join(word for _, word in positions)
    return str(data.get("title") or data.get("display_name") or ""), abstract


def _arxiv_record(arxiv_id: str) -> tuple[str, str] | None:
    query = urllib.parse.urlencode({"id_list": arxiv_id})
    root = ET.fromstring(_get(f"https://export.arxiv.org/api/query?{query}"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        return None
    title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
    summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
    if not summary or title.lower() == "error":
        return None
    return title, summary


def fetch_online(run: Path, key: str, record: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch title and abstract from OpenAlex or arXiv, caching the answer."""
    cache = run / "literature" / f"{key}.online.json"
    if cache.exists():
        return load_json(cache)
    ids = record.get("ids") or {}
    result: tuple[str, str] | None = None
    source = None
    try:
        if ids.get("openalex") or ids.get("doi"):
            result, source = _openalex_record(ids), "openalex"
        if (result is None or not result[1]) and ids.get("arxiv"):
            result, source = _arxiv_record(str(ids["arxiv"])), "arxiv"
    except Exception as exc:  # network errors, bad payloads
        return {"error": f"{type(exc).__name__}: {exc}"}
    if result is None:
        return None
    online = {"source": source, "title": result[0], "abstract": result[1]}
    write_json(cache, online)
    return online


def check_citation(
    run: Path, citation: dict[str, Any], online: bool
) -> dict[str, Any]:
    key = str(citation.get("key", ""))
    snippet = str(citation.get("snippet", ""))
    out: dict[str, Any] = {"key": key, "claim": citation.get("claim", ""), "snippet": snippet}
    if not LIT_KEY.match(key):
        out["status"] = "bad-key"
        return out
    path = run / "literature" / f"{key}.json"
    if not path.exists():
        out["status"] = "missing-record"
        return out
    record = load_json(path)
    abstract = str(record.get("abstract", ""))
    if not snippet:
        out["status"] = "no-snippet"
    elif snippet in abstract:
        out["status"] = "verified"
    elif normalize_text(snippet) in normalize_text(abstract):
        out["status"] = "verified-normalized"
    else:
        out["status"] = "mismatch"
    if online:
        fetched = fetch_online(run, key, record)
        if fetched is None:
            out["online"] = "unavailable"
        elif "error" in fetched:
            out["online"] = f"error: {fetched['error']}"
        elif not str(fetched.get("abstract", "")).strip():
            out["online"] = "unavailable (no abstract online)"
        else:
            title_ratio = difflib.SequenceMatcher(
                None, normalize_text(fetched["title"]), normalize_text(str(record.get("title", "")))
            ).ratio()
            in_source = bool(snippet) and normalize_text(snippet) in normalize_text(fetched["abstract"])
            out["online"] = "verified" if in_source else "snippet-not-in-source"
            if title_ratio < 0.85:
                out["online"] += "; title-mismatch"
    return out


def check_citations(
    run: Path,
    hyps: dict[str, dict[str, Any]],
    full: dict[str, dict[str, Any]],
    online: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    sources = [(f"hypotheses/{h}.json", d) for h, d in hyps.items()]
    sources += [(f"reviews/{h}.json", d) for h, d in full.items()]
    for origin, data in sources:
        for citation in data.get("citations", []) or []:
            if isinstance(citation, dict):
                result = check_citation(run, citation, online)
                result["in"] = origin
                results.append(result)
    return results


# --------------------------------------------------------------------------
# State assembly
# --------------------------------------------------------------------------


def build_state(run: Path, online: bool = False) -> dict[str, Any]:
    problems = Problems()
    config = load_config(run)
    hyps = load_hypotheses(run, problems)
    initial, full = load_reviews(run, problems)
    duplicates = load_duplicates(run, problems)
    games = load_games(run, problems)
    matches = build_matches(games, problems)
    ratings, record = compute_ratings(matches, config)
    tests = load_tests(run, problems)

    for m in matches:
        for side in (m["a"], m["b"]):
            if side not in hyps:
                problems.add(f"{m['id']}: references unknown hypothesis {side}")

    init = float(config["ideate"]["elo_init"])
    entries: list[dict[str, Any]] = []
    for hyp_id in sorted(hyps, key=hyp_number):
        h = hyps[hyp_id]
        status = status_of(hyp_id, initial, full, duplicates, tests)
        review = full.get(hyp_id, {})
        entries.append(
            {
                "id": hyp_id,
                "title": h.get("title", ""),
                "summary": h.get("summary", ""),
                "origin": h.get("origin", ""),
                "strategy": h.get("strategy", ""),
                "parents": h.get("parents", []),
                "round": h.get("round"),
                "status": status,
                "duplicate_of": duplicates.get(hyp_id),
                "elo": round(ratings.get(hyp_id, init), 2),
                "record": record.get(hyp_id, [0, 0, 0]),
                "scores": review.get("scores", {}),
                "review_verdict": review.get("verdict"),
                "test": tests.get(hyp_id, {}).get("verdict"),
            }
        )

    rounds = [
        int(h.get("round") or 0) for h in hyps.values() if h.get("origin") in ("generated", "evolved")
    ]
    h_numbers = [hyp_number(h) for h in hyps]
    h_numbers += [hyp_number(p.name.split(".")[0]) for p in (run / "reviews").glob("H*.json")]
    h_numbers += [hyp_number(p.name) for p in (run / "tests").glob("H*")]
    m_numbers = [int(m[1:]) for m in games]

    return {
        "run_dir": str(run),
        "config": config,
        "hypotheses": entries,
        "matches": matches,
        "ratings": {h: round(r, 6) for h, r in ratings.items()},
        "citations": check_citations(run, hyps, full, online),
        "next_round": max(rounds, default=0) + 1,
        "next_hypothesis": max(h_numbers, default=0) + 1,
        "next_match": max(m_numbers, default=0) + 1,
        "position_disagreements": sum(1 for m in matches if m["position_disagreement"]),
        "problems": problems.items,
    }


def render_ranking(state: dict[str, Any]) -> str:
    rank_statuses = ("active", "test-planned", "tested-awaiting-analysis") + VERDICTS
    ranked = [h for h in state["hypotheses"] if h["status"] in rank_statuses]
    ranked.sort(key=lambda h: (-h["elo"], hyp_number(h["id"])))
    lines = ["# Co-scientist ranking", ""]
    lines.append(f"Run: `{state['run_dir']}`  ")
    lines.append(
        f"Matches: {len(state['matches'])} "
        f"(position disagreements: {state['position_disagreements']})"
    )
    lines.append("")
    lines.append("| # | ID | Title | Elo | W-D-L | Nov. | Plaus. | Test. | Status | Origin |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for i, h in enumerate(ranked, 1):
        s = h["scores"] or {}
        w, d, l = h["record"]
        title = h["title"].replace("|", "/")
        lines.append(
            f"| {i} | {h['id']} | {title} | {h['elo']:.0f} | {w}-{d}-{l} | "
            f"{s.get('novelty', '')} | {s.get('plausibility', '')} | {s.get('testability', '')} | "
            f"{h['status']} | {h['origin']} |"
        )
    others = [h for h in state["hypotheses"] if h["status"] not in rank_statuses]
    if others:
        lines += ["", "## Not ranked", ""]
        for h in others:
            extra = f" (duplicate of {h['duplicate_of']})" if h["duplicate_of"] else ""
            lines.append(f"- {h['id']} [{h['status']}{extra}]: {h['title']}")
    tested = [h for h in state["hypotheses"] if h["test"]]
    if tested:
        lines += ["", "## Tests", ""]
        lines.append("| ID | Verdict | Decided by | Rule decision | Audit holds | Audit issues | Files |")
        lines.append("|---|---|---|---|---|---|---|")
        for h in tested:
            t = h["test"]
            audit = t.get("audit") or {}
            issues = audit.get("issues") or []
            lines.append(
                f"| {h['id']} | {t.get('verdict')} | {t.get('decided_by', '')} | {t.get('rule_decision', '')} | "
                f"{audit.get('verdict_holds', '')} | {len(issues)} | "
                f"`tests/{h['id']}/result.md`, `tests/{h['id']}/audit.md` |"
            )
        lines.append("")
        lines.append("Audit issues that do not change a verdict often correct the analyst's text: read audit.md next to result.md.")
    cites = state["citations"]
    if cites:
        counts: dict[str, int] = {}
        for c in cites:
            counts[c["status"]] = counts.get(c["status"], 0) + 1
        lines += ["", "## Citation checks", ""]
        lines.append(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
        bad = [c for c in cites if c["status"] not in ("verified", "verified-normalized")]
        bad += [c for c in cites if str(c.get("online", "verified")) != "verified" and c not in bad]
        for c in bad:
            online = f", online: {c['online']}" if "online" in c else ""
            lines.append(f"- {c['in']} -> `{c['key']}`: {c['status']}{online}")
    if state["problems"]:
        lines += ["", "## Problems", ""]
        lines += [f"- {p}" for p in state["problems"]]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def write_audit_files(run: Path, state: dict[str, Any]) -> None:
    """Render each verdict's audit block as tests/<id>/audit.md, beside result.md."""
    for h in state["hypotheses"]:
        verdict = h["test"]
        if not verdict:
            continue
        audit = verdict.get("audit") or {}
        issues = audit.get("issues") or []
        lines = [
            f"# Audit of {h['id']}",
            "",
            "Generated by `cs_state.py rebuild` from `verdict.json`; do not edit.",
            "",
            f"- Final verdict: {verdict.get('verdict')} (decided by {verdict.get('decided_by', 'unknown')})",
            f"- Rule decision: {verdict.get('rule_decision', 'unknown')}",
            f"- Audit says the verdict holds: {audit.get('verdict_holds', 'unknown')}",
            "",
            "## Issues",
            "",
        ]
        lines += [f"{i}. {issue}" for i, issue in enumerate(issues, 1)] or ["None reported."]
        (run / "tests" / h["id"] / "audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> int:
    run = Path(args.run_dir).expanduser().resolve()
    for sub in SUBDIRS:
        (run / sub).mkdir(parents=True, exist_ok=True)
    config_path = run / "config.json"
    if config_path.exists():
        print(f"config.json already exists in {run}; left unchanged")
    else:
        config = copy.deepcopy(DEFAULT_CONFIG)
        if args.override:
            config = deep_merge(config, load_json(Path(args.override)))
        write_json(config_path, config)
    print(run)
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    run = Path(args.run_dir).expanduser().resolve()
    state = build_state(run)
    active = [h for h in state["hypotheses"] if h["status"] == "active"]
    unreviewed = [h for h in state["hypotheses"] if h["status"] == "unreviewed"]
    played = sorted({pair_key(m["a"], m["b"]) for m in state["matches"]})
    init = float(state["config"]["ideate"]["elo_init"])
    # Full-precision ratings, so the workflow continues exactly where the files left off.
    ratings = {h["id"]: state["ratings"].get(h["id"], init) for h in active}
    payload = {
        "runDir": str(run),
        "skillDir": str(Path(args.skill_dir).expanduser().resolve()),
        "config": state["config"],
        "prior": {
            "round": state["next_round"],
            "nextHypothesis": state["next_hypothesis"],
            "nextMatch": state["next_match"],
            "ratings": ratings,
            "active": [{"id": h["id"], "title": h["title"]} for h in active],
            "unreviewed": [{"id": h["id"], "title": h["title"]} for h in unreviewed],
            "playedPairs": played,
            "known": [
                {"id": h["id"], "title": h["title"], "status": h["status"]}
                for h in state["hypotheses"]
            ],
        },
    }
    if state["problems"]:
        print(f"warning: {len(state['problems'])} problem(s); run rebuild", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    run = Path(args.run_dir).expanduser().resolve()
    state = build_state(run, online=args.online)
    if args.expect_ratings:
        expected = load_json(Path(args.expect_ratings))
        for hyp_id, value in sorted(expected.items()):
            got = state["ratings"].get(hyp_id)
            if got is None:
                state["problems"].append(f"expected rating for {hyp_id} but it played no match")
            elif abs(got - float(value)) > 0.01:
                state["problems"].append(
                    f"rating mismatch for {hyp_id}: workflow {float(value):.2f}, files {got:.2f}"
                )
    write_json(run / "state.json", state)
    (run / "ranking.md").write_text(render_ranking(state), encoding="utf-8")
    write_audit_files(run, state)
    statuses: dict[str, int] = {}
    for h in state["hypotheses"]:
        statuses[h["status"]] = statuses.get(h["status"], 0) + 1
    print(f"hypotheses: {len(state['hypotheses'])} {statuses}")
    print(f"matches: {len(state['matches'])}, position disagreements: {state['position_disagreements']}")
    print(f"wrote {run / 'state.json'} and {run / 'ranking.md'}")
    for problem in state["problems"]:
        print(f"problem: {problem}")
    return 1 if state["problems"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create a run directory and its config.json")
    p_init.add_argument("--run-dir", required=True)
    p_init.add_argument("--override", help="JSON file merged over the default config")
    p_init.set_defaults(func=cmd_init)

    p_sum = sub.add_parser("summary", help="print the args object for the ideate workflow")
    p_sum.add_argument("--run-dir", required=True)
    p_sum.add_argument("--skill-dir", required=True)
    p_sum.set_defaults(func=cmd_summary)

    p_reb = sub.add_parser("rebuild", help="validate files, recompute Elo, write state.json and ranking.md")
    p_reb.add_argument("--run-dir", required=True)
    p_reb.add_argument("--expect-ratings", help="JSON {id: rating} returned by the workflow")
    p_reb.add_argument("--online", action="store_true", help="check snippets against OpenAlex/arXiv")
    p_reb.set_defaults(func=cmd_rebuild)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

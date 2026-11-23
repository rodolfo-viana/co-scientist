# File formats

The run directory is the source of truth. `scripts/cs_state.py rebuild` reads
these files, recomputes the Elo ratings and writes `state.json` and
`ranking.md`. Write every JSON file with the Write tool, as valid JSON (double
quotes, no comments, no trailing commas). Paths below are relative to the run
directory.

```
goal.md                     research goal and plan configuration (written by the scientist or the main session)
config.json                 run configuration (cs_state.py init)
feedback.md                 optional notes from the scientist; every agent reads it if present
meta_review.md, overview.md written by the meta-reviewer at the end of each round
hypotheses/H003.md|.json    one hypothesis
reviews/H003.initial.json   quick triage
reviews/H003.md|.json       full review
literature/<key>.json       one record per source actually retrieved
matches/M0007-ab.json       one judged game (ab = scheduled order, ba = swapped)
rounds/R01/dedup.json       duplicate clusters of round 1 (generated and user hypotheses)
rounds/R01/dedup-evolved.json  duplicate clusters among the evolved hypotheses of round 1
tests/H003/...              pre-registered test, code, results and verdict
```

## hypotheses/H003.md

Markdown for humans, with these sections in order: `# <title>`, `## Hypothesis`
(one or two sentences), `## Rationale` (mechanism and reasoning),
`## Relation to prior work` (cite as `[key]`, see literature.md),
`## Falsifiable prediction` (what result would refute it),
`## Cheapest decisive test`, `## Assumptions` (numbered list),
`## Risks and failure modes`.

Do not write the origin, strategy, parents or any score in the markdown: the
judge reads this file and must not know where a hypothesis came from.

## hypotheses/H003.json

```json
{
  "id": "H003",
  "title": "Short noun phrase, at most 15 words",
  "summary": "At most 60 words: the claim and why it matters",
  "origin": "generated | evolved | user",
  "strategy": "name of the generation or evolution strategy, or 'user'",
  "parents": ["H001"],
  "round": 1,
  "prediction": "One sentence: the measurable outcome the hypothesis predicts",
  "citations": [{"key": "arxiv-2401.01234", "claim": "what the source supports", "snippet": "exact quote from its abstract"}]
}
```

## reviews/H003.initial.json

```json
{"id": "H003", "stage": "initial", "pass": true, "reason": "One or two sentences",
 "scores": {"alignment": 4, "plausibility": 3, "novelty_prior": 3, "testability": 4}, "safety_ok": true}
```

## reviews/H003.json

Scores are integers from 1 (worst) to 5 (best).

```json
{
  "id": "H003",
  "stage": "full",
  "verdict": "accept | revise | reject",
  "scores": {"alignment": 4, "plausibility": 3, "novelty": 4, "testability": 5, "impact": 3},
  "closest_prior_work": [{"key": "openalex-W123", "relation": "how it overlaps and what remains new"}],
  "fatal_flaws": [],
  "assumptions": [{"text": "...", "status": "supported | unsupported | contradicted | unknown", "fundamental": true}],
  "citations": [{"key": "...", "claim": "...", "snippet": "..."}]
}
```

`reject` means a fatal flaw, a safety problem, or that the core claim is
already established in the literature. `revise` keeps the hypothesis in the
tournament.

## literature/<key>.json

One file per source you actually retrieved with a tool. Never create a record
from memory.

```json
{
  "key": "arxiv-2401.01234",
  "title": "...",
  "authors": ["First Author", "Second Author"],
  "year": 2024,
  "venue": "ICLR 2025 | arXiv preprint",
  "peer_reviewed": true,
  "ids": {"doi": "10.xxxx/...", "arxiv": "2401.01234", "openalex": "W123"},
  "abstract": "copied verbatim from the tool output",
  "retrieved_with": "mcp__openalex__get_work"
}
```

Keys use only letters, digits, `.`, `_` and `-`: `arxiv-<id>`,
`openalex-<W id>`, `doi-<doi with / replaced by _>`. If the file already
exists, reuse it.

## matches/M0007-ab.json

```json
{"match": "M0007", "round": 1, "swiss": 2, "order": "ab", "first": "H003", "second": "H005",
 "winner": "H005", "confidence": "low | medium | high", "rationale": "At most 80 words"}
```

`first` is the hypothesis you were shown as Hypothesis 1. `winner` must be
`first` or `second`.

## rounds/R01/dedup.json and dedup-evolved.json

```json
{"round": 1, "clusters": [{"keep": "H002", "drop": ["H006"], "reason": "same mechanism and same test"}]}
```

## tests/H003/

| File | Written by | Content |
|---|---|---|
| `plan.md`, `plan.json` | test designer | pre-registration, fixed before any result exists |
| `code/`, `launch.sh` | implementer | experiment code and the full launch script |
| `prepare.json` | implementer | smoke-test outcome and cost estimate |
| `results/<arm>/seed_<s>.json` | the experiment code | one file per arm and seed |
| `run.json` | runner | what ran, what failed, deviations |
| `stats.json` | `scripts/seed_stats.py` | paired statistics and the rule decision |
| `result.md`, `verdict.draft.json` | analyst | interpretation and draft verdict |
| `verdict.json` | finalizer | final verdict after the audit |

`plan.json`:

```json
{
  "id": "H003",
  "design": "paired-two-arm | custom",
  "claim": "...",
  "prediction": "...",
  "primary_metric": "val_acc",
  "direction": "higher | lower",
  "min_effect": 0.01,
  "treatment_arm": "treatment",
  "control_arm": "control",
  "seeds": [0, 1, 2, 3, 4],
  "secondary_metrics": [],
  "decision_rule": "for paired-two-arm: the rule in seed_stats.py; for custom: a rule a third party could apply mechanically",
  "dataset": "...",
  "baselines": ["..."],
  "controls": ["..."],
  "confounders": ["..."],
  "tier": "local | gpu-server | not-cheaply-testable",
  "est_minutes": 20,
  "needs_gpu": false,
  "gpu_mem_gb": 0,
  "open_issues": [],
  "round": 1
}
```

`min_effect` is a non-negative number in the units of the primary metric: the
smallest effect that would matter for the claim.

`results/<arm>/seed_<s>.json`:

```json
{"arm": "treatment", "seed": 0, "metrics": {"val_acc": 0.873}, "config": {"lr": 0.01, "seed": 0}}
```

`prepare.json`:

```json
{"id": "H003", "smoke_ok": true, "blocked_reason": null, "est_minutes_full": 12,
 "needs_gpu": false, "gpu_mem_gb": 0, "packages_missing": [], "python": "/path/to/python"}
```

`run.json`:

```json
{"id": "H003", "where": "local | server", "completed": true,
 "seeds_done": {"treatment": [0, 1, 2, 3, 4], "control": [0, 1, 2, 3, 4]},
 "failures": [], "deviations": []}
```

`verdict.draft.json` and `verdict.json`:

```json
{"id": "H003", "verdict": "supported | refuted | inconclusive", "rule_decision": "supported",
 "decided_by": "rule | analyst | audit", "notes": "...", "audit": {"verdict_holds": true, "issues": []}}
```

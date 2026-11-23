---
name: co-scientist
description: Multi-agent co-scientist for ML and AI research. Generates hypotheses for a research goal, reviews them against the literature, ranks them in an Elo tournament with position-swapped judging, evolves the best ones, and tests selected hypotheses with pre-registered seeded experiments and an adversarial audit. Use when the user types /co-scientist, asks to run the co-scientist, to generate, rank or test research hypotheses (also in Portuguese, e.g. "rodar o co-scientist", "gerar hipóteses de pesquisa", "ranquear hipóteses", "testar hipóteses do co-scientist"), or to continue, test or inspect an existing co-scientist run.
---

# Co-scientist

Invoking this skill is the user's request for multi-agent orchestration: it
runs saved workflow scripts that spawn tens of agents per round. Before each
workflow run, state the estimated number of agent calls in one line, then run
it. `<skill>` below is the absolute path of the directory holding this file
(normally `~/.claude/skills/co-scientist`; expand `~` to the home directory
before passing a path to a script or a workflow).

The design follows the Co-Scientist paper (Gottweis et al., "Accelerating
scientific discovery with Co-Scientist", Nature 655, 2026): generation,
reflection, an Elo tournament, evolution and meta-review. The methodology,
writing rules, citation snippet checks and hypothesis status model are
adapted from OpenScientist (Apache License 2.0, see `NOTICE`). The test stage
is new: pre-registered experiments decided by a fixed statistical rule.

## Commands

| Command | What it does |
|---|---|
| `/co-scientist <goal>` | new run: goal, one ideation round, report |
| `/co-scientist continue <run-dir> [notes]` | feedback or own hypotheses, then another round |
| `/co-scientist test <run-dir> H003 H007` | prepare, approve and run tests |
| `/co-scientist status <run-dir>` | rebuild and show the ranking |

Natural-language requests map to the same procedures. Talk to the user in
their language; run files are written in English unless `config.json` sets
`"language": "pt"` (Brazilian Portuguese).

## Setup check

- Every stage runs through the Workflow tool. If it is not available, stop and
  tell the user that co-scientist needs a Claude Code version with dynamic
  workflows, on a paid plan or with an API key, and that on Pro they are
  turned on in `/config` (Dynamic workflows).
- The plan critique and the verdict audit use the `co-scientist-critic`
  subagent shipped in `<skill>/agents/`. Before preparing or running tests,
  run `bash <skill>/install.sh --check`. If it reports the subagent missing or
  outdated, tell the user, run `bash <skill>/install.sh` (it copies one file
  into the `agents/` directory next to `skills/`) and check again. Claude Code
  loads new subagent files without a restart; if the workflow still cannot
  find the subagent, ask the user to restart Claude Code.

## New run

1. Run directory: the one the user names; otherwise
   `<cwd>/co-scientist-runs/<YYYYMMDD-HHMM>-<slug>`, or
   `~/co-scientist-runs/<YYYYMMDD-HHMM>-<slug>` when the cwd is `$HOME` or
   inside `~/.claude`. Get the timestamp from `date +%Y%m%d-%H%M`.
2. `python3 <skill>/scripts/cs_state.py init --run-dir <run>`. For a scale
   preset or model change, write the overrides to a JSON file and pass
   `--override <file>` (see Configuration).
3. Write `<run>/goal.md` from the user's words, with these sections: Title;
   Research goal; Background and known work; Preferences (what kind of
   hypotheses); Constraints (data, compute, time; the local GPU is shown by
   `nvidia-smi`, and any remote server or cluster the user mentions needs
   approval before use); Evaluation criteria (default: alignment, plausibility,
   novelty, testability, safety); Out of scope. Do not invent background. If
   something essential is missing (what counts as success, which compute),
   ask once; if the user told you to proceed on your own, write your choices
   under "Assumptions made by the assistant".
4. Run a round (below).

## A round

1. `python3 <skill>/scripts/cs_state.py summary --run-dir <run> --skill-dir <skill>`
   prints one JSON object.
2. Call Workflow with `scriptPath: "<skill>/workflows/ideate.js"` and `args`
   set to that object (a JSON value, not a string).
3. When it finishes, write the result's `expect_ratings` to
   `<run>/rounds/R<NN>/expect_ratings.json` and run
   `python3 <skill>/scripts/cs_state.py rebuild --run-dir <run> --expect-ratings <that file> --online`.
   `--online` checks citation snippets against OpenAlex and arXiv; it sends
   only paper identifiers. A non-zero exit lists problems: fix JSON syntax
   errors in agent files if that is all they are (note the fix in
   `feedback.md`), and report anything else.
4. Report from `ranking.md`, `overview.md` and the workflow result: the top
   hypotheses with Elo and one line each, the suggested first tests, counts
   of rejected and duplicate hypotheses, citation check counts, and the share
   of matches whose two games disagreed. Above 30%, warn that the judging is
   close to position noise and the ranking is weak evidence.
5. Offer the next step: another round, feedback, the user's own hypotheses,
   or tests of the top ones.

## Continue

- Feedback: append it to `<run>/feedback.md` under `## Before round <N>`.
  Every agent reads this file.
- The user's own hypothesis: take the next id from `summary`, write
  `hypotheses/<id>.md` (sections in `references/file-formats.md`) and
  `hypotheses/<id>.json` with `"origin": "user"`, `"strategy": "user"`,
  `"round": <next round>`, from the user's words only. The next round reviews
  it and puts it in the tournament.
- Then run a round.

## Test

1. Rebuild. Test active hypotheses; test another status only if the user
   insists.
2. Interpreter for the experiments: the one the user names; else
   `<cwd>/.venv/bin/python` or `<cwd>/venv/bin/python` if present; else ask.
   Never install packages globally. Ask before creating a virtual environment
   or downloading large packages, models or datasets.
3. Local GPU string: `nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader`
   (or "none").
4. Workflow `<skill>/workflows/prepare_tests.js` with args
   `{"runDir", "skillDir", "config": <config.json>, "ids": [...], "env": {"python", "local_gpu"}}`.
   About four agents per hypothesis.
5. Show, per hypothesis: status, design, primary metric and direction,
   `min_effect`, seeds, estimated minutes, GPU need, blocked reason, and the
   critique items. Statuses: `ready` (fits the local limits), `needs-server`,
   `blocked` (missing package or download), `not-cheaply-testable`,
   `implementation-failed`.
6. Approval: running experiments needs the user's go-ahead (no long training
   without asking). Exception: if the user has already authorized local tests
   within the configured limits in this conversation, run the `ready` ones.
   - `ready`: Workflow `<skill>/workflows/run_tests.js` with args
     `{"runDir", "skillDir", "config", "items": [{"id": "H003"}], "env": {...}}`.
   - `needs-server`: never automatic. After explicit approval, run
     `tests/<id>/launch.sh` on the server the user chooses. If the user has a
     skill for remote GPU jobs, use it; otherwise give the user the commands
     to copy `tests/<id>/` to the server, run `launch.sh` there and copy the
     results back, and wait until they say it is done. Then check that the
     result files are in `tests/<id>/results/`, write `tests/<id>/run.json`
     with `"where": "server"`, and run `run_tests.js` with
     `{"id": "<id>", "analyzeOnly": true}`.
   - `blocked`: report what is missing and ask.
7. Rebuild and report each verdict with the paired difference, its 95% CI and
   n from `stats.json` (or the test's own analysis output for the custom
   design), who decided it (rule, analyst or audit), and the audit issues.
   `rebuild` writes them to `tests/<id>/audit.md`; they often correct the
   analyst's text even when the verdict holds, so summarize those
   corrections and never repeat a claim from `result.md` that the audit
   disputes. Say plainly that a supported verdict on a small local proxy
   does not establish the hypothesis at scale.

## Status

`cs_state.py rebuild --run-dir <run>`, then summarize `ranking.md`.

## Configuration

`<run>/config.json` (created by `init`) holds:

- `ideate`: `n_generate`, `swiss_rounds`, `n_evolve`, `evolved_swiss_rounds`,
  `top_k`, `elo_k`, `elo_init`. Presets: small `{4, 2, 2, 1}` is about 30
  agent calls; the default `{6, 3, 3, 2}` about 60; large `{10, 4, 5, 3}`
  about 130 in the first round (the workflow logs its own upper bound, which
  grows with the hypotheses carried over).
- `tests`: `smoke_max_minutes`, `local_max_minutes_per_hypothesis`,
  `command_max_minutes` (keep below 10), `default_seeds`.
- `models`: model and effort per role. Defaults: Opus with xhigh effort for
  reasoning and review roles, Sonnet xhigh for code. Judge calls are about
  half of the agent calls in a round, so setting `judge` to Sonnet is the
  cheapest way to lower the cost. The plan critique and the verdict audit use
  the `co-scientist-critic` subagent, whose file sets `model: inherit`.
- `wandb`: experiments log to W&B when enabled and `wandb` is importable;
  keys stay out of the run (offline mode if not logged in).

## Measured cost

One data point, from a smoke run on 2026-11-23 with Sonnet in every role:

| Stage | Agent calls | Subagent tokens | Wall time |
|---|---|---|---|
| Ideation, config `{3, 1, 1, 1}` | 20 | 1.56 M | 7 min |
| Prepare one test | 4 | 0.38 M | 21 min |
| Run, analyze and audit one test | 4 | 0.37 M | 6 min |

First real run, 2026-11-23, default configuration (Opus xhigh):

| Stage | Agent calls | Subagent tokens | Wall time |
|---|---|---|---|
| Ideation, config `{6, 3, 3, 2}` | 63 | 8.55 M | 67 min |

`snippet-not-in-source` from `--online` can be a different version of the
abstract, not a fabricated one: Lacuna serves the OpenReview abstract, while
OpenAlex and arXiv serve the arXiv one. Check the source the record names in
`retrieved_with` before calling a citation wrong.

## Rules

- Do not edit files the agents wrote, except to repair JSON syntax that
  `rebuild` reports. Never change `plan.json` once a result exists.
- References follow `references/literature.md`: only retrieved sources, a
  stored record per source, an exact snippet per citation.
- Report what was not verified: failed agents (`failures` in the workflow
  result), citations not checked online, seeds that did not run.

## Limitations

- Elo measures the judges' preferences, not truth. The position-disagreement
  rate shows how much of it is noise.
- Novelty checks see what the literature tools return (mostly abstracts);
  paywalled full texts are not read.
- Every agent is a Claude model, so blind spots are shared across roles.
- Local tests are small proxies with few seeds; most real effects of modest
  size will come out inconclusive at n = 5.

## Files

`scripts/cs_state.py` (state, Elo, citation checks), `scripts/seed_stats.py`
(statistics and decision rule), `workflows/` (ideate, prepare_tests,
run_tests), `roles/` (one file per agent role), `agents/` (the
`co-scientist-critic` subagent, installed by `install.sh`), `references/`
(file formats, literature, methodology, writing style), `tests/` (pytest and
`node tests/mock_workflows.mjs`, an offline run of all three workflows with
fake agents).

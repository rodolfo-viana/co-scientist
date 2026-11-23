# co-scientist (Claude Code skill)

A multi-agent co-scientist for ML and AI research, run inside Claude Code as
the `/co-scientist` skill. You describe a research goal; it generates
hypotheses, reviews them against the literature, ranks them in an Elo
tournament judged with the presentation order swapped, evolves the best ones,
and, if you ask, tests the ones you pick with pre-registered seeded
experiments and an adversarial audit. Every step writes plain files to a run
directory that you can read, edit and resume.

The design follows the Co-Scientist of Gottweis et al. (Nature, 2026); parts
of the methodology are adapted from OpenScientist. See `NOTICE`.

## Requirements

- **Claude Code with dynamic workflows** (the Workflow tool). They are
  available on paid plans and with API access; on Pro, turn on "Dynamic
  workflows" in `/config`. See the
  [workflows documentation](https://code.claude.com/docs/en/workflows).
  A round starts 30 to 130 agents. If Claude holds back because of the
  workflow size guideline, raise "Dynamic workflow size" in `/config`.
- **Python 3.9 or newer** as `python3`. The scripts use only the standard
  library; nothing to `pip install`.
- **git**, to clone and update.

Optional:

- Literature tools. Agents use, in this order and when present, the Lacuna
  and OpenAlex MCP servers and the reference-lookup tools of the research-desk
  plugin. Without them they fall back to web search and web fetch, which
  works but finds less.
- A local NVIDIA GPU for the test stage (`nvidia-smi` must work).
- `wandb` installed in the Python environment you use for experiments. Tests
  log to W&B when it is importable, in offline mode if you are not logged in.
- Node.js, only to run the development tests.

## Install

Clone into your personal skills directory and install the subagent that the
test stage uses:

```bash
git clone https://github.com/rodolfo-viana/co-scientist.git ~/.claude/skills/co-scientist
```

```bash
bash ~/.claude/skills/co-scientist/install.sh
```

`install.sh` checks the Python version and copies
`agents/co-scientist-critic.md` to `~/.claude/agents/`. It does nothing else.
Claude Code picks up both the skill and the subagent without a restart; if
`/co-scientist` does not appear, restart Claude Code.

To install for a single project instead, clone into
`<project>/.claude/skills/co-scientist` and run the `install.sh` there; the
subagent then goes to `<project>/.claude/agents/`.

To check an installation, run `bash ~/.claude/skills/co-scientist/install.sh --check`.

To update:

```bash
git -C ~/.claude/skills/co-scientist pull && bash ~/.claude/skills/co-scientist/install.sh
```

## Usage

Start Claude Code in the directory of your project and type:

| Command | What it does |
|---|---|
| `/co-scientist <goal>` | new run: writes the goal, runs one ideation round, reports the ranking |
| `/co-scientist continue <run-dir> [notes]` | adds your feedback or your own hypotheses, then runs another round |
| `/co-scientist test <run-dir> H003 H007` | designs, critiques and implements tests, then runs the ones you approve |
| `/co-scientist status <run-dir>` | rebuilds the state and shows the ranking |

Plain requests work too ("run another round of the co-scientist on ...",
"test H003").

Example goal: `/co-scientist cheap ways to make small vision transformers more
robust to label noise, testable on CIFAR-10 on one 24 GB GPU`. The more you
say about data, compute, what counts as success and what is out of scope, the
fewer questions Claude asks before starting.

Runs are written to `./co-scientist-runs/<timestamp>-<slug>/` (or
`~/co-scientist-runs/` when you start from your home directory). Start with
`ranking.md` and `overview.md`; each hypothesis, review, tournament match and
literature record is a separate file.

What it does not do without asking: run experiments, install packages,
download models or datasets, or use a remote server. A test that does not fit
the local limits is marked `needs-server`; after you approve it, Claude
either uses a skill you have for remote GPU jobs or gives you the commands to
run it yourself, then analyzes the results.

## Cost

With the default configuration (Opus with xhigh effort in most roles), one
measured ideation round took 63 agent calls, 8.55 M subagent tokens and 67
minutes. A smaller smoke run with Sonnet in every role took 20 calls and
1.56 M tokens for ideation, and about 0.4 M tokens per tested hypothesis for
each of the prepare and run stages. On a Pro plan a default round can use up
the usage limit, so start small.

To spend less, ask for it when you start a run, for example "use the small
preset and Sonnet for the judge". The small preset is about 30 agent calls
per round, and judge calls are about half of every round. Claude states the
estimated number of agent calls before each workflow starts.

## Configuration

`init` writes `<run>/config.json`. Ask Claude to change it in plain words, or
see the Configuration section of `SKILL.md` for the keys: ideation sizes
(`n_generate`, `swiss_rounds`, `n_evolve`, `evolved_swiss_rounds`), local test
limits, model and effort per role, the language of the run files (`en` or
`pt`) and W&B settings.

## Pipeline

1. Ideation round (`workflows/ideate.js`): generation agents with different
   strategies, an initial triage, a full review with literature search and
   assumption checks, deduplication, a Swiss-system Elo tournament in which
   every match is judged twice with the presentation order swapped, evolution
   of the top hypotheses, a second deduplication pass over the evolved ones,
   a second tournament, and a meta-review that feeds the next round.
2. Test preparation (`workflows/prepare_tests.js`): a pre-registered plan per
   hypothesis, an adversarial critique by the `co-scientist-critic` subagent
   with one revision, then code, a launch script and a smoke run.
3. Test run (`workflows/run_tests.js`): local execution within time limits,
   statistics and the pre-registered decision rule (`scripts/seed_stats.py`),
   an adversarial audit that can only downgrade a verdict, and the final
   verdict file. Server-sized tests run only after approval and come back for
   analysis only.

The run directory is the source of truth. `scripts/cs_state.py rebuild`
validates every file, recomputes Elo from the match files, checks citation
snippets (optionally against OpenAlex and arXiv) and writes `state.json` and
`ranking.md`.

## Limitations

- Elo measures the judges' preferences, not truth. The report gives the share
  of matches whose two games disagreed; above 30% the ranking is close to
  position noise.
- Novelty checks see what the literature tools return, mostly abstracts.
  Paywalled full texts are not read.
- Every agent is a Claude model, so blind spots are shared across roles.
- Local tests are small proxies with few seeds; most real effects of modest
  size come out inconclusive at n = 5, and a supported verdict on a proxy
  does not establish the hypothesis at scale.
- Citations are checked for an exact snippet in the stored abstract, which
  catches invented quotes but not every misreading. Check a reference before
  you cite it.

## Development

```bash
python3 -m pytest -q tests
```

```bash
node tests/mock_workflows.mjs
```

The first command needs `pytest`. The second runs the three workflows with
fake agents that write the same files real agents write, and checks that the
ratings computed by the workflow match the ones rebuilt from the files. No
model is called.

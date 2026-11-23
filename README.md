# co-scientist (Claude Code skill)

A multi-agent co-scientist for ML and AI research, run as a Claude Code skill
(`/co-scientist`) that drives saved workflow scripts. See `SKILL.md` for the
procedure and `NOTICE` for attribution.

## Pipeline

1. Ideation round (`workflows/ideate.js`): generation agents with different
   strategies, an initial triage, a full review with literature search and
   assumption checks, deduplication, a Swiss-system Elo tournament in which
   every match is judged twice with the presentation order swapped, evolution
   of the top hypotheses, a second deduplication pass over the evolved ones,
   a second tournament, and a meta-review that feeds the next round.
2. Test preparation (`workflows/prepare_tests.js`): a pre-registered plan per
   hypothesis, an adversarial critique by the `revisor` agent with one
   revision, then code, a launch script and a smoke run.
3. Test run (`workflows/run_tests.js`): local execution within time limits,
   statistics and the pre-registered decision rule (`scripts/seed_stats.py`),
   an adversarial audit that can only downgrade a verdict, and the final
   verdict file. Server-sized tests go through the `gpu-job` skill after
   approval and come back for analysis only.

The run directory is the source of truth. `scripts/cs_state.py rebuild`
validates every file, recomputes Elo from the match files, checks citation
snippets (optionally against OpenAlex and arXiv) and writes `state.json` and
`ranking.md`.

## Development

```bash
python3 -m pytest -q tests
node tests/mock_workflows.mjs
```

The second command runs the three workflows with fake agents that write the
same files real agents write, and checks that the ratings computed by the
workflow match the ones rebuilt from the files.

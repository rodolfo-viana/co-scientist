# Methodology for ML and AI hypotheses

Adapted from the workflow skills of OpenScientist
(`skills/workflow/{hypothesis-generation,prioritization,result-interpretation,stopping-criteria}`,
https://github.com/openscientist-io/openscientist, Apache License 2.0).
Changes: rewritten for ML and AI research instead of biomedical data
analysis; tests are pre-registered experiments with seeds instead of
statistical tests on a fixed dataset; a non-significant result is no longer
read as a rejection (see "Reading results").

## What a good hypothesis looks like

The form is "intervention or condition X changes outcome Y, through mechanism
Z, under conditions C". Examples:

- Good: "In ViTs trained on ImageNet-1k, the gain from stochastic depth comes
  from reducing co-adaptation of late blocks, so it disappears when the last
  four blocks are frozen after warm-up."
- Too vague: "Regularization matters for transformers."
- Not testable here: "Scaling to 10T parameters yields emergent reasoning."

Requirements:

- Falsifiable: state the result that would refute it.
- Mechanistic where possible: a correlation alone is a weak hypothesis.
- Testable within the compute, data and time stated in `goal.md`. If the full
  test needs more, name a cheaper proxy test and say what it can and cannot
  show.
- Not already established. Check the literature (literature.md) and the
  hypotheses already in the run, including those refuted by tests.

## Before proposing

- Read `goal.md`, `feedback.md`, `meta_review.md` and `overview.md` if they
  exist, and `state.json` for what was already proposed, rejected or tested.
- Do not repeat a refuted hypothesis. A refuted test narrows the space: build
  on what it ruled out.
- Do not repeat a hypothesis that another one already covers with a different
  wording.

## Prioritizing what to test

Weigh, in this order: how directly the hypothesis answers the research goal;
whether the cheapest decisive test fits the available compute; how much the
answer would change what the scientist does next (either outcome); novelty.
A cheap test that could refute a central assumption beats an expensive test of
a peripheral one.

## Designing a test

- Fix the decision rule before any result exists (pre-registration). Write the
  primary metric, its direction, the smallest effect that would matter
  (`min_effect`), the arms and the seeds.
- Prefer the paired two-arm design: treatment and control share everything
  (data order, initialization, budget) except the intervention, and each seed
  runs both arms. Pair by seed.
- Use at least 5 seeds when the run is cheap. With 3 seeds the 95% interval
  uses t = 4.30 and most effects come out inconclusive.
- Give the baseline the same tuning budget as the treatment. An untuned
  baseline makes any treatment look good.
- Name the confounders the design does not control, and the controls it adds
  (for example a shuffled-label control, or an arm that changes compute but
  not the intervention).
- Check for leakage: test data must not influence training, early stopping or
  checkpoint selection. Use a validation split for any choice.
- A pilot with about 5 seeds underestimates the spread of skewed or
  point-mass-plus-tail statistics (for example a gain that is zero in most
  seeds and large in a few). Size the main run on a conservative spread, not
  on the pilot's sample SD.
- A sensitivity control must have the same noise as the real effect. An
  injected effect that the procedure picks up in every seed shows the code
  path works, not that the test can detect an effect of that size.

## Reading results

The paired-two-arm rule in `scripts/seed_stats.py` decides mechanically:

- supported: the 95% CI of the oriented difference excludes 0 and the mean
  reaches `min_effect`;
- refuted: the upper CI bound is below `min_effect`, so an effect of the
  predicted size is ruled out;
- inconclusive: anything else, including fewer than two paired seeds.

A non-significant result is inconclusive, not a refutation. "No evidence of an
effect" and "evidence of no effect" are different claims.

The analyst may downgrade a rule decision to inconclusive (a crashed seed, a
deviation from the plan, a bug), and must never upgrade it. The audit may also
downgrade. Before accepting any verdict, look for: evaluation bugs (wrong
split, metric computed wrongly, test set used for selection), leakage, seed
variance larger than the effect, an unfair baseline, and a result that holds
only for one configuration.

Report effect sizes with intervals, not only a verdict. A surprising result is
first a suspected bug: check the code and the logs before interpreting it.

## When to stop a round

Stop generating when new proposals repeat existing ones or only restate them
with different words. Stop testing when the top hypotheses have verdicts and
the remaining ones would not change the scientist's next decision. Three well
supported findings are worth more than ten marginal ones.

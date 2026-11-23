#!/usr/bin/env python3
"""Paired seed statistics and the pre-registered decision rule.

Reads ``plan.json`` and ``results/<arm>/seed_<s>.json`` from a test
directory and writes ``stats.json``. For the ``paired-two-arm`` design the
decision is mechanical:

* fewer than two paired seeds               -> inconclusive
* upper 95% CI bound of the oriented
  difference below ``min_effect``           -> refuted
* lower bound above 0 and mean >= min_effect -> supported
* anything else                             -> inconclusive

"Oriented" means the difference is signed so that a positive value is the
effect the hypothesis predicts. A non-significant result is never read as a
refutation: refuting requires the interval to exclude the predicted effect
size. The ``custom`` design only gets descriptive statistics.

Usage:
    seed_stats.py --test-dir RUN/tests/H003
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

# Two-sided 95% Student t critical values.
T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980,
}
SEED_FILE = re.compile(r"^seed_(-?\d+)\.json$")


def t_crit(df: int) -> float:
    """Critical value for the largest tabulated df <= df (conservative)."""
    if df < 1:
        raise ValueError("df must be >= 1")
    usable = [d for d in T95 if d <= df]
    return T95[max(usable)]


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def sample_sd(xs: list[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def load_arm(results_dir: Path, arm: str, warnings: list[str]) -> dict[int, dict[str, Any]]:
    runs: dict[int, dict[str, Any]] = {}
    arm_dir = results_dir / arm
    if not arm_dir.is_dir():
        warnings.append(f"no results directory for arm {arm!r}")
        return runs
    for path in sorted(arm_dir.glob("seed_*.json")):
        match = SEED_FILE.match(path.name)
        if not match:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"{arm}/{path.name}: unreadable ({exc})")
            continue
        metrics = data.get("metrics")
        if not isinstance(metrics, dict):
            warnings.append(f"{arm}/{path.name}: missing 'metrics' object")
            continue
        runs[int(match.group(1))] = metrics
    return runs


def describe(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": mean(values) if values else None,
        "sd": sample_sd(values) if len(values) > 1 else None,
    }


def decide(diffs: list[float], min_effect: float) -> tuple[dict[str, Any], str, str]:
    n = len(diffs)
    if n < 2:
        return {"n": n}, "inconclusive", "fewer than two paired seeds"
    m = mean(diffs)
    sd = sample_sd(diffs)
    se = sd / math.sqrt(n)
    t = t_crit(n - 1)
    lo, hi = m - t * se, m + t * se
    paired = {
        "n": n,
        "mean_diff": m,
        "sd_diff": sd,
        "se": se,
        "t_crit": t,
        "ci95": [lo, hi],
        "cohens_dz": (m / sd) if sd > 0 else None,
    }
    if hi < min_effect:
        return paired, "refuted", (
            f"95% CI upper bound {hi:.4g} is below the pre-registered minimal effect {min_effect:.4g}"
        )
    if lo > 0 and m >= min_effect:
        return paired, "supported", (
            f"95% CI [{lo:.4g}, {hi:.4g}] excludes 0 and the mean {m:.4g} reaches the minimal effect {min_effect:.4g}"
        )
    return paired, "inconclusive", (
        f"95% CI [{lo:.4g}, {hi:.4g}] neither excludes 0 with mean >= {min_effect:.4g} "
        f"nor rules out the minimal effect"
    )


def compute(test_dir: Path) -> dict[str, Any]:
    plan = json.loads((test_dir / "plan.json").read_text(encoding="utf-8"))
    warnings: list[str] = []
    design = plan.get("design", "paired-two-arm")
    metric = plan.get("primary_metric")
    results_dir = test_dir / "results"
    out: dict[str, Any] = {
        "id": plan.get("id", test_dir.name),
        "design": design,
        "primary_metric": metric,
        "warnings": warnings,
    }
    if design != "paired-two-arm":
        arms = sorted(p.name for p in results_dir.iterdir() if p.is_dir()) if results_dir.is_dir() else []
        out["arms"] = {}
        for arm in arms:
            runs = load_arm(results_dir, arm, warnings)
            values = [float(r[metric]) for r in runs.values() if metric in r]
            out["arms"][arm] = describe(values)
        out["rule_decision"] = "none"
        out["rule_reason"] = "custom design: apply the written decision rule in plan.json"
        return out

    direction = plan.get("direction")
    if direction not in ("higher", "lower"):
        raise ValueError("plan.json: direction must be 'higher' or 'lower'")
    min_effect = float(plan.get("min_effect", 0.0))
    if min_effect < 0:
        raise ValueError("plan.json: min_effect must be >= 0 (it is oriented by direction)")
    treat = plan.get("treatment_arm", "treatment")
    ctrl = plan.get("control_arm", "control")
    sign = 1.0 if direction == "higher" else -1.0

    t_runs = load_arm(results_dir, treat, warnings)
    c_runs = load_arm(results_dir, ctrl, warnings)
    seeds = sorted(set(t_runs) & set(c_runs))
    unpaired = sorted(set(t_runs) ^ set(c_runs))
    if unpaired:
        warnings.append(f"seeds present in only one arm were ignored: {unpaired}")
    planned = plan.get("seeds")
    if isinstance(planned, list) and sorted(planned) != seeds:
        warnings.append(f"planned seeds {sorted(planned)} differ from paired seeds {seeds}")

    diffs: list[float] = []
    used: list[int] = []
    for s in seeds:
        if metric not in t_runs[s] or metric not in c_runs[s]:
            warnings.append(f"seed {s}: metric {metric!r} missing in one arm")
            continue
        diffs.append(sign * (float(t_runs[s][metric]) - float(c_runs[s][metric])))
        used.append(s)

    out.update({"direction": direction, "min_effect": min_effect})
    out["arms"] = {
        arm: describe([float(r[metric]) for r in runs.values() if metric in r])
        for arm, runs in ((treat, t_runs), (ctrl, c_runs))
    }
    paired, decision, reason = decide(diffs, min_effect)
    paired["seeds"] = used
    if paired.get("sd_diff") == 0:
        warnings.append("zero variance of the difference across seeds: check that the seed changes the run")
    out["paired"] = paired
    out["secondary"] = {}
    for sec in plan.get("secondary_metrics", []) or []:
        out["secondary"][sec] = {
            arm: describe([float(r[sec]) for r in runs.values() if sec in r])
            for arm, runs in ((treat, t_runs), (ctrl, c_runs))
        }
    out["rule_decision"] = decision
    out["rule_reason"] = reason
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test-dir", required=True)
    parser.add_argument("--stdout", action="store_true", help="print instead of writing stats.json")
    args = parser.parse_args(argv)
    test_dir = Path(args.test_dir).expanduser().resolve()
    stats = compute(test_dir)
    text = json.dumps(stats, indent=2, ensure_ascii=False) + "\n"
    if args.stdout:
        print(text, end="")
    else:
        (test_dir / "stats.json").write_text(text, encoding="utf-8")
        print(f"{stats['rule_decision']}: {stats['rule_reason']}")
        for w in stats["warnings"]:
            print(f"warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import json
from pathlib import Path

import pytest

import seed_stats


def make_test(tmp_path: Path, plan: dict, results: dict[str, dict[int, float]], metric: str = "acc") -> Path:
    d = tmp_path / "H001"
    d.mkdir()
    (d / "plan.json").write_text(json.dumps(plan))
    for arm, by_seed in results.items():
        for seed, value in by_seed.items():
            p = d / "results" / arm / f"seed_{seed}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"arm": arm, "seed": seed, "metrics": {metric: value}}))
    return d


def plan(**kw):
    base = {
        "id": "H001",
        "design": "paired-two-arm",
        "primary_metric": "acc",
        "direction": "higher",
        "min_effect": 0.01,
        "treatment_arm": "treatment",
        "control_arm": "control",
        "seeds": [0, 1, 2, 3, 4],
    }
    base.update(kw)
    return base


def test_t_crit_is_conservative_between_table_entries():
    assert seed_stats.t_crit(4) == pytest.approx(2.776)
    assert seed_stats.t_crit(45) == pytest.approx(2.021)
    with pytest.raises(ValueError):
        seed_stats.t_crit(0)


def test_supported_when_ci_excludes_zero_and_mean_reaches_min_effect(tmp_path):
    ctrl = {s: 0.80 + 0.001 * s for s in range(5)}
    treat = {s: 0.85 + 0.001 * s + (0.002 if s % 2 else 0) for s in range(5)}
    d = make_test(tmp_path, plan(), {"treatment": treat, "control": ctrl})
    out = seed_stats.compute(d)
    assert out["rule_decision"] == "supported"
    assert out["paired"]["n"] == 5


def test_refuted_requires_ci_below_min_effect_not_just_nonsignificance(tmp_path):
    ctrl = {s: 0.80 for s in range(5)}
    treat = {s: 0.80 + (0.001 if s % 2 else -0.001) for s in range(5)}
    d = make_test(tmp_path, plan(min_effect=0.02), {"treatment": treat, "control": ctrl})
    assert seed_stats.compute(d)["rule_decision"] == "refuted"


def test_noisy_small_sample_is_inconclusive(tmp_path):
    ctrl = {0: 0.80, 1: 0.80, 2: 0.80}
    treat = {0: 0.90, 1: 0.78, 2: 0.86}
    d = make_test(tmp_path, plan(seeds=[0, 1, 2]), {"treatment": treat, "control": ctrl})
    assert seed_stats.compute(d)["rule_decision"] == "inconclusive"


def test_direction_lower_orients_the_difference(tmp_path):
    ctrl = {s: 1.0 + 0.01 * s for s in range(4)}
    treat = {s: 0.5 + 0.01 * s + (0.003 if s == 1 else 0) for s in range(4)}
    d = make_test(
        tmp_path,
        plan(primary_metric="loss", direction="lower", min_effect=0.1, seeds=[0, 1, 2, 3]),
        {"treatment": treat, "control": ctrl},
        metric="loss",
    )
    out = seed_stats.compute(d)
    assert out["rule_decision"] == "supported"
    assert out["paired"]["mean_diff"] > 0


def test_single_seed_and_unpaired_seeds(tmp_path):
    d = make_test(tmp_path, plan(seeds=[0, 1]), {"treatment": {0: 0.9, 1: 0.9}, "control": {0: 0.8}})
    out = seed_stats.compute(d)
    assert out["rule_decision"] == "inconclusive"
    assert any("only one arm" in w for w in out["warnings"])


def test_custom_design_gets_descriptives_only(tmp_path):
    d = make_test(tmp_path, plan(design="custom"), {"probe": {0: 0.3, 1: 0.5}})
    out = seed_stats.compute(d)
    assert out["rule_decision"] == "none"
    assert out["arms"]["probe"]["n"] == 2


def test_invalid_direction_raises(tmp_path):
    d = make_test(tmp_path, plan(direction="up"), {"treatment": {0: 1}, "control": {0: 1}})
    with pytest.raises(ValueError):
        seed_stats.compute(d)

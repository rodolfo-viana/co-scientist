"""Checks that the skill works from a fresh clone on another machine."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = "co-scientist-critic"
TEXT_SUFFIXES = {".md", ".js", ".mjs", ".py", ".sh", ".json"}

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def repo_text_files() -> list[Path]:
    skip = {".git", ".pytest_cache", "__pycache__", "LICENSES"}
    return [
        p
        for p in ROOT.rglob("*")
        if p.is_file() and p.suffix in TEXT_SUFFIXES and not skip.intersection(p.relative_to(ROOT).parts)
    ]


def frontmatter_name(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\A---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path.name}: no frontmatter"
    names = re.findall(r"^name:\s*(\S+)\s*$", match.group(1), re.M)
    assert len(names) == 1, f"{path.name}: expected one name field"
    return names[0]


def test_bundled_agent_matches_workflows():
    assert frontmatter_name(ROOT / "agents" / f"{AGENT}.md") == AGENT
    for name in ("prepare_tests.js", "run_tests.js"):
        source = (ROOT / "workflows" / name).read_text(encoding="utf-8")
        assert f"const CRITIC_AGENT = '{AGENT}'" in source, name
        assert re.findall(r"agentType:\s*([^\s,}]+)", source) == ["CRITIC_AGENT"], name
    assert "agentType" not in (ROOT / "workflows" / "ideate.js").read_text(encoding="utf-8")


def test_no_machine_specific_paths():
    home_path = re.compile("/" + "home/|/" + "Users/")
    offenders = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in repo_text_files()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if home_path.search(line)
    ]
    assert not offenders, offenders


def run_install(script: Path, *args: str, agents_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "CO_SCIENTIST_AGENTS_DIR"}
    if agents_dir is not None:
        env["CO_SCIENTIST_AGENTS_DIR"] = str(agents_dir)
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)


@needs_bash
def test_install_check_install_update(tmp_path: Path):
    script = ROOT / "install.sh"
    dest = tmp_path / "agents" / f"{AGENT}.md"
    assert run_install(script, "--check", agents_dir=dest.parent).returncode == 1
    assert not dest.exists()

    assert run_install(script, agents_dir=dest.parent).returncode == 0
    assert dest.read_bytes() == (ROOT / "agents" / f"{AGENT}.md").read_bytes()
    assert run_install(script, "--check", agents_dir=dest.parent).returncode == 0

    dest.write_text("old version\n", encoding="utf-8")
    assert run_install(script, "--check", agents_dir=dest.parent).returncode == 1
    assert run_install(script, agents_dir=dest.parent).returncode == 0
    assert dest.with_name(dest.name + ".bak").read_text(encoding="utf-8") == "old version\n"
    assert run_install(script, "--check", agents_dir=dest.parent).returncode == 0

    assert run_install(script, "--bogus", agents_dir=dest.parent).returncode == 2


@needs_bash
def test_install_finds_agents_dir_next_to_skills(tmp_path: Path):
    skill = tmp_path / ".claude" / "skills" / "co-scientist"
    (skill / "agents").mkdir(parents=True)
    shutil.copy2(ROOT / "install.sh", skill / "install.sh")
    shutil.copy2(ROOT / "agents" / f"{AGENT}.md", skill / "agents" / f"{AGENT}.md")
    result = run_install(skill / "install.sh")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".claude" / "agents" / f"{AGENT}.md").exists()

    elsewhere = tmp_path / "src" / "co-scientist"
    elsewhere.mkdir(parents=True)
    shutil.copy2(ROOT / "install.sh", elsewhere / "install.sh")
    assert run_install(elsewhere / "install.sh").returncode == 2

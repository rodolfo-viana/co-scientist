#!/usr/bin/env bash
# Install the subagent that ships with the co-scientist skill.
#
# Usage: install.sh [--check]
#
# The skill must live in <config>/skills/co-scientist, where <config> is
# ~/.claude (personal) or <project>/.claude (one project). The subagent is
# copied to <config>/agents/. With --check, nothing is written and the exit
# status says whether the installation is complete and up to date.
# CO_SCIENTIST_AGENTS_DIR overrides the target directory.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_NAME="co-scientist-critic"
SRC="${SKILL_DIR}/agents/${AGENT_NAME}.md"
MIN_PYTHON="3.9"

if [[ -n "${CO_SCIENTIST_AGENTS_DIR:-}" ]]; then
  AGENTS_DIR="${CO_SCIENTIST_AGENTS_DIR}"
elif [[ "$(basename "$(dirname "${SKILL_DIR}")")" == "skills" ]]; then
  AGENTS_DIR="$(dirname "$(dirname "${SKILL_DIR}")")/agents"
else
  echo "error: ${SKILL_DIR} is not inside a skills/ directory." >&2
  echo "Clone the repository into ~/.claude/skills/co-scientist (or <project>/.claude/skills/co-scientist)." >&2
  exit 2
fi
DEST="${AGENTS_DIR}/${AGENT_NAME}.md"

check_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found (version ${MIN_PYTHON} or newer is required)" >&2
    return 1
  fi
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "error: python3 ${MIN_PYTHON} or newer is required, found $(python3 --version 2>&1)" >&2
    return 1
  fi
}

case "${1:-}" in
  --check)
    status=0
    check_python || status=1
    if [[ ! -f "${DEST}" ]]; then
      echo "missing: ${DEST} (run bash ${SKILL_DIR}/install.sh)"
      status=1
    elif ! cmp -s "${SRC}" "${DEST}"; then
      echo "outdated: ${DEST} (run bash ${SKILL_DIR}/install.sh)"
      status=1
    else
      echo "ok: ${DEST}"
    fi
    exit "${status}"
    ;;
  "")
    ;;
  *)
    echo "usage: $0 [--check]" >&2
    exit 2
    ;;
esac

check_python
mkdir -p "${AGENTS_DIR}"
if [[ -f "${DEST}" ]] && cmp -s "${SRC}" "${DEST}"; then
  echo "up to date: ${DEST}"
  exit 0
fi
if [[ -f "${DEST}" ]]; then
  cp "${DEST}" "${DEST}.bak"
  echo "previous version saved to ${DEST}.bak"
fi
cp "${SRC}" "${DEST}"
echo "installed: ${DEST}"
echo "Claude Code picks up new subagents within a few seconds; if /co-scientist cannot find it, restart Claude Code."

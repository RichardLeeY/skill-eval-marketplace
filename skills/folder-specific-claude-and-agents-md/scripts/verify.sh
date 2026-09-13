#!/usr/bin/env bash
# Verify a folder-scoped CLAUDE.md, and create the AGENTS.md symlink if missing.
#
# Creating the symlink here rather than leaving it to the agent is deliberate: it
# is one `ln -s` with two ways to get it wrong (a copy instead of a link, or a link
# written from the wrong working directory so it dangles), and both failures are
# invisible until another agent reads AGENTS.md and gets nothing.
#
# Usage: bash verify.sh <folder>
# Prints exactly one PASS: or FAIL: line. Exit 0 only on PASS.

set -uo pipefail

folder="${1:-}"
if [ -z "$folder" ]; then
  echo "FAIL: usage: verify.sh <folder>"
  exit 2
fi

folder="${folder%/}"
md="$folder/CLAUDE.md"
agents="$folder/AGENTS.md"

fail() {
  echo "FAIL: $md — $1"
  exit 1
}

[ -d "$folder" ] || fail "no such directory: $folder"
[ -f "$md" ] || fail "CLAUDE.md does not exist"
[ -s "$md" ] || fail "CLAUDE.md is empty"

# Relative link target, so the pair survives the folder being moved or copied.
if [ ! -e "$agents" ] && [ ! -L "$agents" ]; then
  ( cd "$folder" && ln -s CLAUDE.md AGENTS.md ) || fail "could not create the AGENTS.md symlink"
fi

[ -L "$agents" ] || fail "AGENTS.md exists but is not a symlink — a copy will drift out of sync"
target=$(readlink "$agents")
case "$target" in
  CLAUDE.md | ./CLAUDE.md) ;;
  *) fail "AGENTS.md points at '$target', not CLAUDE.md" ;;
esac
[ -e "$agents" ] || fail "AGENTS.md is a dangling symlink (target '$target' does not resolve)"

# Shape checks. Each one maps to a rule in SKILL.md that is cheap to get wrong and
# expensive to notice later.
grep -q '^## ' "$md" || fail "no '##' section headers"

if grep -qE '[├└│]|^[[:space:]]*\|--' "$md"; then
  fail "contains a directory tree — structure is derivable from LS and rots on the next commit"
fi

# A parent CLAUDE.md means this file is a subdirectory file and must say so, or the
# next agent applies it without the repo-wide rules it assumes.
parent="$folder"
has_parent_md=""
while [ "$parent" != "." ] && [ "$parent" != "/" ] && [ -n "$parent" ]; do
  parent=$(dirname "$parent")
  if [ -f "$parent/CLAUDE.md" ]; then
    has_parent_md="$parent/CLAUDE.md"
    break
  fi
done

if [ -n "$has_parent_md" ]; then
  grep -qF 'Apply root CLAUDE.md first, then this file.' "$md" ||
    fail "$has_parent_md exists, so the file must carry the line 'Apply root CLAUDE.md first, then this file.'"
fi

sections=$(grep -c '^## ' "$md")
echo "PASS: $md ($sections sections, AGENTS.md -> CLAUDE.md)"

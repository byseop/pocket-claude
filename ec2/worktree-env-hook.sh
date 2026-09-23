#!/bin/bash
# SessionStart hook: give a worktree session the ignored files it needs.
#
# Claude Code creates session worktrees under <repo>/.claude/worktrees/ and
# copies nothing into them, so a project whose runtime needs .env fails in a
# fresh session. Copy only files git ignores, never overwrite, and print
# nothing: hook stdout becomes session context.
set -u

# git prints --git-common-dir relative and --git-dir absolute when the
# session starts in a subdirectory, so compare normalized absolute paths
# (--path-format needs git 2.31+; the box runs 2.43).
GIT_COMMON=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
GIT_DIR=$(git rev-parse --path-format=absolute --git-dir 2>/dev/null) || exit 0
[ "$GIT_COMMON" = "$GIT_DIR" ] && exit 0          # main checkout: nothing to do

WT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
MAIN=$(cd "$(dirname "$GIT_COMMON")" && pwd -P)
[ -d "$MAIN" ] || exit 0

for rel in .env .env.local .env.development.local .vercel/project.json; do
  src="$MAIN/$rel"
  dst="$WT_ROOT/$rel"
  [ -f "$src" ] || continue
  [ -e "$dst" ] && continue
  git -C "$MAIN" check-ignore -q "$rel" || continue   # only ignored files
  mkdir -p "$(dirname "$dst")"
  # cp's exit status is deliberately discarded: the hook must stay silent
  # and always exit 0, even if a single copy fails.
  cp -p "$src" "$dst"
done
exit 0

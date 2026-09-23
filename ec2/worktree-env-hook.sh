#!/bin/bash
# SessionStart hook: give a worktree session the ignored files it needs.
#
# Claude Code creates session worktrees under <repo>/.claude/worktrees/ and
# copies nothing into them, so a project whose runtime needs .env fails in a
# fresh session. Copy only files git ignores, never overwrite, and print
# nothing: hook stdout becomes session context.
set -u

CWD=$(pwd -P)
GIT_COMMON=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
[ "$GIT_COMMON" = "$GIT_DIR" ] && exit 0          # main checkout: nothing to do

MAIN=$(cd "$(dirname "$GIT_COMMON")" && pwd -P)
[ -d "$MAIN" ] || exit 0

for rel in .env .env.local .env.development.local .vercel/project.json; do
  src="$MAIN/$rel"
  dst="$CWD/$rel"
  [ -f "$src" ] || continue
  [ -e "$dst" ] && continue
  git -C "$MAIN" check-ignore -q "$rel" || continue   # only ignored files
  mkdir -p "$(dirname "$dst")"
  cp -p "$src" "$dst"
done
exit 0

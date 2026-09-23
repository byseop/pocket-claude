#!/bin/bash
# Exercises the SessionStart hook that copies ignored files into a worktree.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
HOOK=$HERE/../ec2/worktree-env-hook.sh
FAILS=0
ALL_TMP=()
trap 'rm -rf "${ALL_TMP[@]}"' EXIT

assert_eq() {
  if [ "$1" = "$2" ]; then echo "ok   $3"
  else echo "FAIL $3"; echo "     expected: $2"; echo "     got:      $1"; FAILS=$((FAILS + 1)); fi
}

setup() {
  TMP=$(mktemp -d)
  ALL_TMP+=("$TMP")
  MAIN=$TMP/main
  mkdir -p "$MAIN"
  git -C "$MAIN" init -q
  git -C "$MAIN" config user.email t@example.com
  git -C "$MAIN" config user.name test
  printf '.env\n.env.local\n.vercel/\n' > "$MAIN/.gitignore"
  echo hello > "$MAIN/README.md"
  git -C "$MAIN" add -A
  git -C "$MAIN" commit -qm init
  printf 'SECRET=1\n' > "$MAIN/.env"
  printf 'LOCAL=1\n' > "$MAIN/.env.local"
  mkdir -p "$MAIN/.vercel"; printf '{}\n' > "$MAIN/.vercel/project.json"
  WT=$MAIN/.claude/worktrees/w1
  git -C "$MAIN" worktree add -q -b worktree-w1 "$WT" >/dev/null 2>&1
}

# 1. main checkout: does nothing
setup
(cd "$MAIN" && bash "$HOOK")
assert_eq "$(ls -a "$MAIN" | grep -c '^\.env$' || true)" "1" "main checkout is untouched"

# 2. worktree: ignored files are copied
setup
OUT=$( cd "$WT" && bash "$HOOK" )
assert_eq "$(cat "$WT/.env" 2>/dev/null)" "SECRET=1" "copies .env"
assert_eq "$(cat "$WT/.env.local" 2>/dev/null)" "LOCAL=1" "copies .env.local"
assert_eq "$([ -f "$WT/.vercel/project.json" ] && echo yes || echo no)" "yes" "copies .vercel/project.json"

# 3. hook prints nothing (its stdout would become session context)
assert_eq "$OUT" "" "hook output is empty"

# 4. existing files are not overwritten
setup
printf 'MINE=1\n' > "$WT/.env"
(cd "$WT" && bash "$HOOK")
assert_eq "$(cat "$WT/.env")" "MINE=1" "does not overwrite an existing file"

# 5. tracked files are never copied
setup
rm -f "$WT/README.md"
(cd "$WT" && bash "$HOOK")
assert_eq "$([ -f "$WT/README.md" ] && echo yes || echo no)" "no" "tracked files are left alone"

# 6. outside a repo it exits quietly
setup
OUT2=$( cd "$TMP" && bash "$HOOK" ); RC=$?
assert_eq "$RC" "0" "exits 0 outside a repo"
assert_eq "$OUT2" "" "prints nothing outside a repo"

# 7. main checkout subdirectory is untouched
setup
mkdir -p "$MAIN/sub/deep"
(cd "$MAIN/sub/deep" && bash "$HOOK")
assert_eq "$([ -f "$MAIN/sub/deep/.env" ] && echo yes || echo no)" "no" "main checkout subdirectory is untouched"

# 8. worktree subdirectory still copies to the worktree root
setup
mkdir -p "$WT/sub"
(cd "$WT/sub" && bash "$HOOK")
assert_eq "$([ -f "$WT/.env" ] && echo yes || echo no)" "yes" "worktree subdirectory still copies to the worktree root"
assert_eq "$([ -f "$WT/sub/.env" ] && echo yes || echo no)" "no" "worktree subdirectory still copies to the worktree root"

[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }

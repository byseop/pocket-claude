#!/bin/bash
# Exercises the pure helpers of ec2/claude-rc-wrap.sh. The script is sourced,
# not run, so no tmux or systemd is involved.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=../ec2/claude-rc-wrap.sh
. "$HERE/../ec2/claude-rc-wrap.sh"

FAILS=0
assert_eq() {
  if [ "$1" = "$2" ]; then
    echo "ok   $3"
  else
    echo "FAIL $3"; echo "     expected: $2"; echo "     got:      $1"
    FAILS=$((FAILS + 1))
  fi
}
assert_contains() {
  case "$1" in
    *"$2"*) echo "ok   $3" ;;
    *) echo "FAIL $3"; echo "     missing:  $2"; echo "     in:       $1"; FAILS=$((FAILS + 1)) ;;
  esac
}

# A fake POCKET_ROOT with one project, so rc_workdir/rc_command/rc_trusted
# can be exercised without touching the real box layout.
TMPDIR_FAKE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_FAKE"' EXIT

export POCKET_ROOT="$TMPDIR_FAKE/work"
mkdir -p "$POCKET_ROOT/myapp/.git"
assert_eq "$(rc_workdir myapp)" "$(cd "$POCKET_ROOT/myapp" && pwd -P)" "workdir resolves under POCKET_ROOT"

CMD=$(rc_command myapp)
assert_contains "$CMD" 'claude remote-control' "server mode"
assert_contains "$CMD" '--name "myapp"' "session name is the project name"
assert_contains "$CMD" '--spawn worktree' "spawn mode defaults to worktree"
assert_contains "$CMD" '--capacity 3' "capacity is 1 + POCKET_SESSIONS(2)"
assert_contains "$CMD" '--permission-mode default' "permission mode"
assert_contains "$CMD" '--no-chrome' "chrome off"

POCKET_SESSIONS=4 CMD2=$(rc_command myapp)
assert_contains "$CMD2" '--capacity 5' "capacity follows POCKET_SESSIONS"

POCKET_SPAWN=same-dir CMD3=$(rc_command myapp)
assert_contains "$CMD3" '--spawn same-dir' "spawn mode can be overridden per project"

# A malformed POCKET_SESSIONS must fall back to the default instead of
# breaking arithmetic expansion or reaching the command tmux runs.
POCKET_SESSIONS=3abc CMD4=$(rc_command myapp)
assert_contains "$CMD4" '--capacity 3' "non-numeric POCKET_SESSIONS falls back to default(2)"

# An unrecognized POCKET_SPAWN must fall back to worktree instead of being
# interpolated verbatim into the command tmux hands to a shell.
POCKET_SPAWN=bogus CMD5=$(rc_command myapp)
assert_contains "$CMD5" '--spawn worktree' "unknown POCKET_SPAWN falls back to worktree"

case "$(cat "$HERE/../ec2/claude-rc-wrap.sh")" in
  *'CLAUDE_CODE_OAUTH_TOKEN='*|*'. /home/ubuntu/.claude/.env'*|*'source /home/ubuntu/.claude/.env'*)
    echo "FAIL wrapper must never export or source the OAuth token"; FAILS=$((FAILS + 1)) ;;
  *) echo "ok   wrapper never exports the OAuth token" ;;
esac

# Validate session names: rc_valid_name rejects injection attempts and path traversal
rc_valid_name feat-x; assert_eq "$?" 0 "accepts lowercase with dash"
rc_valid_name ops; assert_eq "$?" 0 "accepts 'ops'"
rc_valid_name 'a;b'; assert_eq "$?" 1 "rejects semicolon"
rc_valid_name 'a b'; assert_eq "$?" 1 "rejects space"
rc_valid_name '../x'; assert_eq "$?" 1 "rejects path traversal"
rc_valid_name '$(x)'; assert_eq "$?" 1 "rejects command substitution"
rc_valid_name 'Feat'; assert_eq "$?" 1 "rejects uppercase"
rc_valid_name ''; assert_eq "$?" 1 "rejects empty"
rc_valid_name "$(printf 'a%.0s' {1..25})"; assert_eq "$?" 1 "rejects name longer than 24 chars"

# Every tmux call in main must name the per-unit socket rc-<name>, so each
# unit owns its own tmux server (cgroup isolation + its own EnvironmentFile).
SOCKETS=$(grep -c 'tmux -L "rc-$name"' "$HERE/../ec2/claude-rc-wrap.sh")
[ "$SOCKETS" -ge 3 ] \
  && echo "ok   every tmux call uses the per-unit socket" \
  || { echo "FAIL expected >=3 'tmux -L \"rc-\$name\"' calls, got $SOCKETS"; FAILS=$((FAILS + 1)); }

# rc_trusted reads the project's real path from POCKET_CLAUDE_JSON.
REALDIR=$(rc_workdir myapp)

TRUSTED_JSON="$TMPDIR_FAKE/trusted.json"
cat > "$TRUSTED_JSON" <<JSON
{"projects": {"$REALDIR": {"hasTrustDialogAccepted": true}}}
JSON
CLAUDE_JSON="$TRUSTED_JSON" rc_trusted "$REALDIR"
assert_eq "$?" 0 "rc_trusted returns 0 for a trusted project"

EMPTY_JSON="$TMPDIR_FAKE/empty.json"
echo '{"projects": {}}' > "$EMPTY_JSON"
CLAUDE_JSON="$EMPTY_JSON" rc_trusted "$REALDIR"
assert_eq "$?" 1 "rc_trusted returns 1 when the project is not trusted"

# A missing CLAUDE_JSON must fail closed (untrusted), never crash or pass.
CLAUDE_JSON="$TMPDIR_FAKE/does-not-exist.json" rc_trusted "$REALDIR"
assert_eq "$?" 1 "rc_trusted returns non-zero when CLAUDE_JSON is missing"

# main must refuse to start an untrusted workspace: starting it anyway fails
# immediately and systemd would restart it until the start limit trips.
( CLAUDE_JSON="$EMPTY_JSON" main myapp )
assert_eq "$?" 1 "main exits 1 for an untrusted workspace"

[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }

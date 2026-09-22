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

assert_eq "$(rc_workdir ops)" "/home/ubuntu/gamer4info" "ops runs in the main checkout"
assert_eq "$(rc_workdir feat-x)" "/home/ubuntu/worktrees/feat-x" "other names run in a worktree"

CMD=$(rc_command feat-x)
assert_contains "$CMD" 'claude remote-control' "command is the server mode"
assert_contains "$CMD" '--name "gamer4-feat-x"' "session name carries the gamer4 prefix"
assert_contains "$CMD" '--spawn same-dir' "spawn mode is same-dir"
assert_contains "$CMD" '--capacity 2' "capacity is capped at 2"
assert_contains "$CMD" '--permission-mode default' "permission mode is default"
assert_contains "$CMD" '--no-chrome' "chrome is off"

case "$(cat "$HERE/../ec2/claude-rc-wrap.sh")" in
  *'CLAUDE_CODE_OAUTH_TOKEN='*|*'. /home/ubuntu/.claude/.env'*|*'source /home/ubuntu/.claude/.env'*)
    echo "FAIL wrapper must never export or source the OAuth token"; FAILS=$((FAILS + 1)) ;;
  *) echo "ok   wrapper never exports the OAuth token" ;;
esac

[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }

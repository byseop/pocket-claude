#!/bin/bash
# Wrapper that systemd can track for one Remote Control server session.
# Running `tmux new-session -d` directly from ExecStart does not work: the
# tmux server daemonizes itself (double fork + setsid), systemd loses the
# PID, decides the service died, and Restart= spins forever.
#
# Usage: claude-rc-wrap.sh <name>     (systemd passes %i)
#   ops    -> /home/ubuntu/gamer4info        (main checkout, enabled at boot)
#   other  -> /home/ubuntu/worktrees/<name>  (git worktree, started by /new)
#
# The OAuth token in ~/.claude/.env is deliberately NOT sourced here. Remote
# Control refuses to start when CLAUDE_CODE_OAUTH_TOKEN is set ("API-key auth
# takes precedence"); the session must run on the claude.ai login stored in
# ~/.claude/.credentials.json.

set -u

REPO=/home/ubuntu/gamer4info
WORKTREES=/home/ubuntu/worktrees
IDLE_STATE=/home/ubuntu/.claude-idle-state
CREDS=/home/ubuntu/.claude/.credentials.json
ENV_FILE=/home/ubuntu/.claude/.env

rc_workdir() {
  if [ "$1" = ops ]; then echo "$REPO"; else echo "$WORKTREES/$1"; fi
}

rc_command() {
  printf 'claude remote-control --name "gamer4-%s" --spawn same-dir --capacity 2 --permission-mode default --no-chrome' "$1"
}

# An expired .credentials.json used to mask CLAUDE_CODE_OAUTH_TOKEN in the
# --channels path. That token is gone in v5, so this guard only fires if
# someone puts the token back into .env. Kept on purpose: the failure mode
# was hard to diagnose and cheap to prevent.
guard_expired_credentials() {
  [ -f "$CREDS" ] || return 0
  grep -q 'CLAUDE_CODE_OAUTH_TOKEN' "$ENV_FILE" 2>/dev/null || return 0
  if ! python3 - "$CREDS" <<'PY'
import json, sys, time
try:
    exp = json.load(open(sys.argv[1]))['claudeAiOauth']['expiresAt'] / 1000
except Exception:
    sys.exit(0)          # unreadable or unexpected shape: treat as valid, leave it
sys.exit(0 if exp > time.time() else 1)
PY
  then
    mv "$CREDS" "$CREDS.expired"
    logger -t claude-rc-wrap "moved expired .credentials.json aside; using CLAUDE_CODE_OAUTH_TOKEN"
  fi
}

# `claude --bg` hands work to an already-running supervisor daemon. A daemon
# that came up while auth was broken keeps that state and fails every
# dispatch after it. Drop a daemon older than the current credentials; leave
# a newer one alone so in-flight background work survives a restart.
guard_stale_daemon() {
  local ref
  if [ -f "$CREDS" ]; then ref=$CREDS; elif [ -f "$ENV_FILE" ]; then ref=$ENV_FILE; else return 0; fi
  local pid elapsed started ref_mtime
  pid=$(pgrep -u "$(id -u)" -f 'claude daemon run' 2>/dev/null | head -1)
  [ -n "$pid" ] || return 0
  elapsed=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
  [ -n "$elapsed" ] || return 0
  started=$(( $(date +%s) - elapsed ))
  ref_mtime=$(stat -c %Y "$ref" 2>/dev/null || echo 0)
  if [ "$ref_mtime" -gt "$started" ]; then
    pkill -u "$(id -u)" -f 'claude daemon run' 2>/dev/null || true
    rm -rf "/tmp/cc-daemon-$(id -u)"
    logger -t claude-rc-wrap "dropped supervisor daemon older than current credentials"
  fi
}

main() {
  local name=${1:?usage: claude-rc-wrap.sh <name>}
  local session="rc-$name"
  local dir
  dir=$(rc_workdir "$name")

  export HOME=/home/ubuntu
  export NVM_DIR=/home/ubuntu/.nvm
  [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
  export BUN_INSTALL=/home/ubuntu/.bun
  export PATH="/home/ubuntu/.local/bin:/home/ubuntu/.bun/bin:$PATH"

  if [ ! -d "$dir" ]; then
    logger -t claude-rc-wrap "workdir $dir does not exist for $name"
    exit 1
  fi

  # Clear the stale idle counter. Without this, a boot right after an idle
  # shutdown inherits the old timestamp and shuts down again immediately.
  rm -f "$IDLE_STATE"

  guard_expired_credentials
  guard_stale_daemon

  tmux has-session -t "$session" 2>/dev/null || \
    tmux new-session -d -s "$session" -c "$dir" "$(rc_command "$name")"

  # Block while the session lives. Exit non-zero when it dies so systemd
  # restarts it (Restart=on-failure).
  while tmux has-session -t "$session" 2>/dev/null; do
    sleep 10
  done
  exit 1
}

# Run main only when executed, so tests can source the helpers above.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi

#!/bin/bash
# Wrapper that systemd can track. Running `tmux new-session -d` directly from
# ExecStart does not work: the tmux server daemonizes itself (double fork +
# setsid), systemd loses the PID, decides the service died, and Restart=always
# spins forever.

set -u

SESSION=claude
BOOT_SCRIPT=/home/ubuntu/start-claude-telegram.sh
IDLE_STATE=/home/ubuntu/.claude-idle-state
CREDS=/home/ubuntu/.claude/.credentials.json
ENV_FILE=/home/ubuntu/.claude/.env

# Clear the stale idle counter. Without this, a boot right after an idle
# shutdown inherits counter=12 and shuts down again immediately.
rm -f "$IDLE_STATE"

# An expired .credentials.json takes precedence over CLAUDE_CODE_OAUTH_TOKEN in
# the interactive (--channels) path: the session picks it up, fails to refresh,
# and reports "401 OAuth access token has been revoked" without ever falling
# back to the env token. Re-issuing the token does nothing, which makes this
# look like the token keeps getting revoked. Move it aside when it is both
# expired and redundant. A still-valid file is left untouched.
if [ -f "$CREDS" ] && grep -q 'CLAUDE_CODE_OAUTH_TOKEN' "$ENV_FILE" 2>/dev/null; then
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
    logger -t claude-supervise "moved expired .credentials.json aside; using CLAUDE_CODE_OAUTH_TOKEN"
  fi
fi

# `claude --bg` hands work to an already-running supervisor instead of starting
# a fresh one. A supervisor that came up while auth was broken keeps that state
# and fails every dispatch after it, including brand-new sessions — so the main
# session looks healthy while every background job returns 401. Drop a
# supervisor that predates the current credentials; leave a newer one alone so
# in-flight background work survives an ordinary restart.
if [ -f "$ENV_FILE" ]; then
  DAEMON_PID=$(pgrep -u "$(id -u)" -f 'claude daemon run' 2>/dev/null | head -1)
  if [ -n "$DAEMON_PID" ]; then
    ELAPSED=$(ps -o etimes= -p "$DAEMON_PID" 2>/dev/null | tr -d ' ')
    if [ -n "$ELAPSED" ]; then
      STARTED=$(( $(date +%s) - ELAPSED ))
      ENV_MTIME=$(stat -c %Y "$ENV_FILE" 2>/dev/null || echo 0)
      if [ "$ENV_MTIME" -gt "$STARTED" ]; then
        pkill -u "$(id -u)" -f 'claude daemon run' 2>/dev/null || true
        rm -rf "/tmp/cc-daemon-$(id -u)"
        logger -t claude-supervise "dropped supervisor daemon older than current credentials"
      fi
    fi
  fi
fi

tmux has-session -t "$SESSION" 2>/dev/null || \
  tmux new-session -d -s "$SESSION" "$BOOT_SCRIPT"

# Block while the session lives. Exit non-zero when it dies so systemd restarts.
while tmux has-session -t "$SESSION" 2>/dev/null; do
  sleep 10
done

exit 1

#!/bin/bash
# Wrapper that systemd can track. Running `tmux new-session -d` directly from
# ExecStart does not work: the tmux server daemonizes itself (double fork +
# setsid), systemd loses the PID, decides the service died, and Restart=always
# spins forever.

set -u

SESSION=claude
BOOT_SCRIPT=/home/ubuntu/start-claude-telegram.sh
IDLE_STATE=/home/ubuntu/.claude-idle-state

# Clear the stale idle counter. Without this, a boot right after an idle
# shutdown inherits counter=12 and shuts down again immediately.
rm -f "$IDLE_STATE"

tmux has-session -t "$SESSION" 2>/dev/null || \
  tmux new-session -d -s "$SESSION" "$BOOT_SCRIPT"

# Block while the session lives. Exit non-zero when it dies so systemd restarts.
while tmux has-session -t "$SESSION" 2>/dev/null; do
  sleep 10
done

exit 1

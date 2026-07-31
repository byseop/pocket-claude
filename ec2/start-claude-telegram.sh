#!/bin/bash
# Launched inside the tmux session by claude-supervise.sh. Sets up the
# toolchain PATH, sources the OAuth token, and execs Claude Code with the
# Telegram channel plugin attached.
export HOME=/home/ubuntu
cd /home/ubuntu

export NVM_DIR=/home/ubuntu/.nvm
source "$NVM_DIR/nvm.sh"
export BUN_INSTALL=/home/ubuntu/.bun
export PATH="/home/ubuntu/.local/bin:/home/ubuntu/.bun/bin:$PATH"

[ -f ~/.claude/.env ] && source ~/.claude/.env

exec /home/ubuntu/.local/bin/claude --dangerously-skip-permissions --channels plugin:telegram@claude-plugins-official

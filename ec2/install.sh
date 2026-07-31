#!/bin/bash
# Installs the supervisor and systemd unit. Idempotent: safe to re-run.
set -eu

install -o ubuntu -g ubuntu -m 0755 /tmp/claude-supervise.sh /home/ubuntu/claude-supervise.sh
install -o root -g root -m 0644 /tmp/claude-telegram.service /etc/systemd/system/claude-telegram.service

systemctl daemon-reload
systemctl enable claude-telegram

echo "installed"

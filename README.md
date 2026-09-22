# pocket-claude

Run Claude Code Remote Control sessions on an EC2 box, talk to them from the
Claude mobile app, and use a tiny Telegram bot as the power switch.

## Why

The instance is off most of the time. Something outside it has to turn it
on, and that something is a Lambda behind API Gateway. Everything else —
the conversation, permission prompts, diffs — goes through Anthropic's
Remote Control channel to the phone app, so the bot never relays chat.

## Architecture

```
[Telegram boot bot]  /start /stop /status /sessions /new <name> /kill <name> /rm <name>
   └─ API GW ─▶ Lambda ec2-boot-manager (v5)
                 ├─ ec2:StartInstances / StopInstances
                 └─ ssm:SendCommand   (systemctl start/stop claude-rc@*, read-only queries)

[EC2 (ubuntu)]
   systemd claude-rc@.service (template)
     ├─ claude-rc@ops   ~/gamer4info          enabled, starts at boot
     └─ claude-rc@<x>   ~/worktrees/<x>       created by /new, not enabled
   each unit: claude-rc-wrap.sh ─▶ tmux rc-<name> ─▶ claude remote-control --name gamer4-<name>
   cron (5 min): idle-watch.sh ─▶ Telegram notice, then stop after 60 idle minutes

[Claude mobile app] ── Anthropic ── EC2 claude process   (no inbound ports)
```

The Lambda never launches Claude. It flips systemd units; systemd owns the
process lifetime. An earlier version launched the session remotely over
SSM and every failure mode traced back to that decision (post-mortem in
`docs/OPERATIONS.md`).

## Commands

| Command | What it does |
|---|---|
| `/start` | Start the instance. `gamer4-ops` appears in the app about a minute later |
| `/stop` | Stop the instance. Every session goes offline |
| `/status` | Instance state, `claude-rc@*` units, auth health |
| `/sessions` | One line per session: active, branch, last conversation |
| `/new <name>` | `git worktree add ~/worktrees/<name>` + `systemctl start claude-rc@<name>` |
| `/kill <name>` | Stop the unit. Worktree and branch stay |
| `/rm <name>` | Stop, then remove the worktree. Refused if it has uncommitted changes; reports removal failure and a missing worktree separately |

`<name>` is `[a-z0-9-]{1,24}`; `ops` is reserved. Nothing blocks for long:
API Gateway allows 30 s and `/new` only waits for `systemctl start`.

## What "ending a session" means

There is no end. A Remote Control session is online while its process
lives and offline a few seconds after it dies. `/kill` stops one unit;
`/stop` or the idle watcher stops the box and takes every session with it.
On the next boot only `ops` comes back. If the box restarts within about
four hours the server session resumes; later than that it is a new
session, and continuity lives in the repo's docs (OPERATIONS), not in the
chat. Commit, push and update OPERATIONS before closing a session.

## Idle detection

The screen hash of v4 is gone: a Remote Control session can be busy while
the terminal never changes. Activity is any of a written transcript under
`~/.claude/projects`, a fresh non-terminal `claude --bg` job, or CPU burned
by a `claude` process since the last sample. Sixty minutes without any of
them stops the instance after a Telegram notice.

## Permissions

`ec2/claude-settings.json` is the user-level `~/.claude/settings.json`:
`default` mode, read-only tools allowed, production operations in `ask`
(they prompt on the phone in every mode, including `auto`), `aws iam` and
`.env` reads denied. Never press "always allow" on a production prompt: it
writes an `allow` rule and the gate is gone. Everything in `secrets.env` is
part of the session's environment, so never ask the session to print `env`;
the boot bot's token lives in `telegram.env`, which only the idle watcher
reads.

## Setup

`docs/SETUP.md` (Korean) is the one-time checklist: claude.ai login on the
instance, tool logins, `secrets.env`, settings, the instance-role policy in
`iam/`, `ec2/install.sh`, and the interactive first start.

Lambda: copy `.env.example` to `.env`, set the same values on the
`ec2-boot-manager` function, point the bot webhook at API Gateway. The
execution role needs `ec2:Start/Stop/DescribeInstances`, `ssm:SendCommand`,
`ssm:GetCommandInvocation`, `ssm:DescribeInstanceInformation`.

## Layout

```
src/lambda_function.py        Lambda handler (v5)
tests/                        unittest for the Lambda helpers, bash tests for the EC2 scripts
ec2/claude-rc@.service        systemd template unit
ec2/claude-rc-wrap.sh         tmux wrapper systemd tracks
ec2/claude-rc.sudoers         ubuntu may start/stop claude-rc@* only
ec2/idle-watch.sh             idle watcher
ec2/install.sh                installer (idempotent)
ec2/claude-settings.json      ~/.claude/settings.json template
ec2/secrets.env.example       ~/.config/gamer4/secrets.env template
ec2/telegram.env.example      ~/.config/gamer4/telegram.env template (idle watcher only)
ec2-claude-md-patch.md        rules to append to the instance's ~/.claude/CLAUDE.md
iam/                          instance-role policy templates
docs/SETUP.md                 one-time setup checklist
docs/OPERATIONS.md            runbook: diagnosis, deployment, incident record
docs/superpowers/             design and implementation-plan docs
```

## Tests

```bash
python3 -m unittest discover -s tests
bash tests/test_idle_watch.sh
bash tests/test_claude_rc_wrap.sh
```

## License

MIT

# pocket-claude

Run Claude Code Remote Control sessions for any number of projects on one
EC2 box, talk to them from the Claude mobile app, and use a tiny Telegram
bot as the power switch and project list.

## Why

The instance is off most of the time. Something outside it has to turn it
on, and that something is a Lambda behind API Gateway. Everything else —
the conversation, permission prompts, diffs — goes through Anthropic's
Remote Control channel to the phone app, so the bot never relays chat.

## Architecture

```
[Telegram bot]  /start /stop /status /projects /up <p> /down <p> /trees <p>
   └─ API GW ─▶ Lambda ec2-boot-manager (v6)
                 ├─ ec2:StartInstances / StopInstances
                 └─ ssm:SendCommand   (runs `pocket <verb> --json` as ubuntu)

[EC2 (ubuntu)]
   ~/work/myapp -> ~/myapp                    symlink; the repo is not moved
   ~/bin/pocket                                CLI: all box logic lives here
   systemd claude-rc@.service (template)
     └─ claude-rc@myapp   ~/work/myapp         one unit per project
   claude-rc@myapp: claude-rc-wrap.sh ─▶ tmux rc-myapp ─▶
     claude remote-control --name myapp --spawn worktree --capacity N --no-chrome

[Claude mobile app] ── Anthropic ── EC2 claude process   (no inbound ports)
```

`pocket` owns the box's layout (projects under `~/work`, systemd units,
session worktrees) and is the only thing on the box that shells out; the
Lambda calls one `pocket` verb over SSM and turns the JSON answer into
Korean text and buttons. The Lambda never launches Claude — systemd owns
the process lifetime (post-mortem in `docs/OPERATIONS.md`).

## Commands

| Command | What it does |
|---|---|
| `/start` / `/stop` | EC2 on/off. On boot, whatever projects were left enabled come back up |
| `/status` | EC2 uptime, per-project server state, memory/disk, auth |
| `/projects` | One line per project (state, branch, last conversation) with buttons |
| `/up <p>` / `/down <p>` | Start+enable, or stop+disable, one project's server |
| `/trees <p>` | That project's session worktrees (dirty/unpushed/locked/in-use) |

`<p>` is `[a-z0-9][a-z0-9-]{0,23}`. A project is a directory (or a symlink
to one) under `~/work` on the box; adding one needs no code change or
redeploy — only a link and `pocket trust <p>` (`docs/SETUP.md`).

## Sessions and worktrees

Each project's server runs with `--spawn worktree`: a "new session" in the
app creates a worktree at `<repo>/.claude/worktrees/<session-id>` on branch
`worktree-<session-id>`. Claude Code locks the worktree while it uses it;
whether that lock clears itself when the session ends wasn't measured, so
`pocket prune` treats a locked worktree as kept regardless of its age —
a stale lock costs disk, never data. The server also
pre-creates one session in the main checkout for repo-wide work, so a
project's effective capacity is `1 + POCKET_SESSIONS` (default 2).
Worktrees accumulate; on the box, `pocket prune <p>` removes only the ones
that are clean, pushed, unlocked and unused — see `docs/OPERATIONS.md`.

## What "ending a session" means

There is no end. A Remote Control session is online while its process
lives and offline a few seconds after it dies. `/down <p>` stops that
project's unit and disables it, so a reboot won't bring it back; `/stop`
takes every project offline with the box. `/up <p>` enables and starts, so
the next boot restores whatever was left running (sticky state). If the
box restarts within about four hours the server session resumes; later
than that it's a new session, and continuity lives in the project's own
docs, not in the chat.

## Stopping

Idle auto-stop was tried and dropped: a quiet Remote Control server still
burns enough CPU every sample that a CPU-based watcher never saw it as
idle. Stopping is manual — `/stop`, or `/down <p>` for one project — and
`/status` shows uptime so a box left running isn't easy to forget.

## Permissions

`ec2/claude-settings.json` is the user-level `~/.claude/settings.json`:
`default` mode, read-only tools allowed, an `ask` list for anything that
changes something outside the box, and `.env`/credential reads denied.
Never press "always allow" on a prompt: it writes a permanent `allow` rule
and the gate is gone. A project's own `.claude/settings.json` can add
project-specific `ask` rules on top.

## Setup

`docs/SETUP.md` (Korean) is the one-time checklist: claude.ai login on the
instance, tool logins, `ec2/install.sh`, linking a project under `~/work`
and `pocket trust`, the instance-role policy in `iam/`, and the
interactive first start.

Lambda: copy `.env.example` to `.env`, set the same values on the
`ec2-boot-manager` function, point the bot webhook at API Gateway. The
execution role needs `ec2:Start/Stop/DescribeInstances`, `ssm:SendCommand`,
`ssm:GetCommandInvocation`, `ssm:DescribeInstanceInformation`.

## Layout

```
src/lambda_function.py        Lambda handler: pocket caller + Telegram
tests/                        unittest for pocket/Lambda, bash tests for the EC2 scripts
ec2/pocket                    CLI: list/up/down/trust/status/trees/prune
ec2/claude-rc@.service        systemd template unit (one instance per project)
ec2/claude-rc.slice           combined memory cap for every claude-rc@ instance
ec2/claude-rc-wrap.sh         tmux wrapper systemd tracks
ec2/claude-rc.sudoers         ubuntu may start/stop/enable/disable claude-rc@* only
ec2/worktree-env-hook.sh      SessionStart hook: copies ignored files into a new worktree
ec2/install.sh                installer (idempotent)
ec2/migrate-v6.sh             one-time v5 -> v6 migration (idempotent)
ec2/idle-watch.sh             idle watcher, kept but not registered (see docs/OPERATIONS.md)
ec2/claude-settings.json      ~/.claude/settings.json template
ec2/secrets.env.example       ~/.config/pocket-claude/secrets.env template
ec2/telegram.env.example      ~/.config/pocket-claude/telegram.env template (currently unused)
ec2-claude-md-patch.md        rules to append to the instance's ~/.claude/CLAUDE.md
iam/                          instance-role policy templates
docs/SETUP.md                 one-time setup checklist
docs/OPERATIONS.md            runbook: diagnosis, deployment, incident record
docs/superpowers/             design and implementation-plan docs
```

## Tests

```bash
python3 -m unittest discover -s tests
bash tests/test_claude_rc_wrap.sh
bash tests/test_worktree_env_hook.sh
bash tests/test_idle_watch.sh
```

## License

MIT

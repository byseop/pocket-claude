# pocket-claude

Run Claude Code on an EC2 box and drive it from Telegram on your phone.

Two bots, two jobs:

- **Boot-manager bot** — an AWS Lambda behind API Gateway that turns the EC2 instance on and off and reports its state. It answers even when the instance is powered down, which is the whole point of keeping it outside the box.
- **Main Claude bot** — the Telegram channel plugin running inside a `claude` session on the instance. This is the one you actually talk to.

They use separate bot tokens because a single token cannot serve a webhook and long polling at the same time.

## Why not just SSH from your phone

Because the instance is off most of the time. Something has to be able to turn it on, and that something cannot live on the instance. The Lambda is that something, and it is deliberately kept as small as possible.

## Architecture

```
[Telegram]
 ├─ @<boot-manager-bot> ──webhook──▶ API Gateway ──▶ Lambda (ec2-boot-manager)
 │     /start /stop /status /view              ├─ ec2:Start/StopInstances
 │                                              └─ ssm:SendCommand  (read-only queries)
 └─ @<main-claude-bot> ──long polling──▶ claude session on EC2

[EC2 instance]
 systemd: claude-telegram.service (Restart=always)
    └─ claude-supervise.sh
         └─ tmux 'claude'
              └─ claude --dangerously-skip-permissions --channels plugin:telegram@claude-plugins-official
 cron (5 min): idle-watch.sh ──▶ stops the instance after 60 idle minutes
```

The instance starts Claude Code itself via systemd. The Lambda never reaches in to launch anything — it is a power switch and a viewport, nothing more. An earlier version did launch the session remotely over SSM, and every failure mode traced back to that one decision. See the design doc for the post-mortem.

## Commands

| Command | What it does | Latency |
|---|---|---|
| `/start` | Starts the instance and returns immediately | < 1s |
| `/stop` | Stops the instance | < 1s |
| `/status` | Instance state, service health, auth status | 2–3s |
| `/view` | Current tmux screen, ANSI stripped | 2–3s |

Nothing blocks. This matters: API Gateway's integration timeout is 30 s, and an earlier version that waited on an `instance_running` waiter took 38.7 s and got a 503.

## Setup

You need an AWS account, an EC2 instance with the SSM agent, and two Telegram bots from [@BotFather](https://t.me/BotFather).

1. **Configure the Lambda.** Copy `.env.example` to `.env` and fill it in, then set the same values as environment variables on the `ec2-boot-manager` function. `.env` is gitignored — no identifiers belong in this repository.

2. **IAM.** The Lambda's execution role needs `ec2:Start/Stop/DescribeInstances` and `ssm:SendCommand`, `ssm:GetCommandInvocation`, `ssm:DescribeInstanceInformation`. The instance role needs `AmazonSSMManagedInstanceCore`, plus `ec2:StopInstances` scoped to its own ARN if you want idle auto-shutdown.

3. **Point the webhook at API Gateway.**
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<api-id>.execute-api.<region>.amazonaws.com/prod/webhook"
   ```

4. **On the instance**, install `claude-supervise.sh`, the systemd unit, and the idle watcher, then `systemctl enable --now claude-telegram`.

## Auth

Claude Code on the instance authenticates with a long-lived OAuth token in `~/.claude/.env`:

```bash
claude setup-token
umask 077
echo 'export CLAUDE_CODE_OAUTH_TOKEN=<token>' > ~/.claude/.env
sudo systemctl restart claude-telegram
```

`claude setup-token` issues the token but does **not** write it to `.credentials.json` — you have to export it yourself. When the token is revoked or expires, the bot process stays alive and only the replies fail, which is indistinguishable from a hang unless you look. That is why `/status` reports auth state explicitly.

## Layout

```
src/lambda_function.py   Lambda handler
backup/                  earlier versions, kept for reference
docs/superpowers/specs/  design docs
.env.example             configuration template
```

## License

MIT

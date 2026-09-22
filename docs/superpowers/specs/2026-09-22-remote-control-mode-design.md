# pocket-claude v5 — Remote Control 모드 (설계)

작성 2026-09-22 (gamer4info 오퍼레이터 세션에서 작성, 조사 보고서 `docs/research/2026-09-22-ec2-remote-control-report.md` 기반). 상태: 사용자 설계 검토 대기 → 승인 후 `writing-plans`로 계획 작성.

## 0. 한 줄 요약

텔레그램 `/start`로 EC2를 켜면 systemd가 Claude Code **Remote Control 서버 세션**을 자동으로 띄우고, 사용자는 **모바일 Claude 앱**에서 대화한다. 텔레그램 봇은 전원 스위치 + 세션 유닛 관리(`/new` `/kill` `/sessions`)만 맡는다. 기존 텔레그램 채널 플러그인(메인 클로드 봇)은 폐기한다.

## 1. 요구사항 (사용자, 2026-09-22)

1. 람다로 EC2를 켤 때 Remote Control이 켜져서 모바일 앱에서 바로 쓸 수 있어야 한다.
2. 텔레그램에서 **새 세션을 띄우는 명령**이 필요하다. 구현 방식은 오퍼레이터 판단.
3. **세션 종료 의미**를 정리해야 한다("그냥 두면 종료로 보는가").
4. AWS·Vercel 등 **로그인이 필요한 항목**을 목록화한다(사용자가 EC2에서 직접 수행).

## 2. 확정 사실 (조사 보고서 §2, CLI 실측·공식 문서)

- `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN`으로는 Remote Control 세션을 만들 수 없다(모델 요청 전용). **`claude auth login`(claude.ai OAuth) → `~/.claude/.credentials.json`이 필수.** `~/.claude/.env`에 토큰이 남아 있으면 "API-key auth 우선"으로 거부된다.
- `ec2/claude-supervise.sh`의 만료-credentials 가드는 `.env`에 `CLAUDE_CODE_OAUTH_TOKEN`이 있을 때만 발동 → 토큰을 지우면 자동 무력화. **가드 코드는 지우지 않는다.**
- 서버 모드 `claude remote-control`: `--spawn same-dir|worktree|session`, `--capacity N`(기본 32), `--name`, `--permission-mode`, `-c/--continue`, `--session-id`. 인터랙티브 `claude --remote-control`은 프로세스당 1세션.
- 공식 권고: SSH 끊김 뒤 세션 유지는 **tmux 안에서**. 워크스페이스 신뢰·"Enable Remote Control?" 수락 2건은 대화형 1회.
- 정지 후 **약 4시간 이내** 같은 디렉터리에서 서버 재기동 시 세션 복귀, 그 뒤엔 새 세션. 네트워크 단절 시 서버 모드는 ~10분 뒤 프로세스 종료(systemd가 재기동).
- `permissions.ask` 규칙은 auto·bypassPermissions를 포함한 **모든 모드에서 자동 승인되지 않는다** → 승인 게이트는 settings로 구현. 프로젝트 `.claude/settings.json`의 `auto`/`bypassPermissions`는 무시됨(user settings 또는 CLI 플래그만).
- 세션당 메모리(맥 실측): 유휴 121MB, 포화 518MB. 24GB EBS는 워크트리 2~3개가 상한(각 ~700MB).
- 인바운드 포트 0. 트랜스크립트는 Anthropic 서버에 저장 → `.env` 읽기 deny 필요.

## 3. 설계

### 3-1. 구성 요소

```
[Telegram boot bot] /start /stop /status /sessions /new <name> /kill <name>
   └─ API GW ─▶ Lambda ec2-boot-manager (v5)
                 ├─ ec2:StartInstances / StopInstances
                 └─ ssm:SendCommand ── systemctl / git worktree / claude 상태 조회
[EC2 (ubuntu)]
   systemd claude-rc@.service (템플릿) ── tmux rc-<name> ── claude remote-control --name gamer4-<name>
   ├─ claude-rc@ops   : WorkingDirectory ~/gamer4info (메인 checkout), enable → 부팅 자동
   └─ claude-rc@<x>   : WorkingDirectory ~/worktrees/<x>, /new 로 생성, enable 안 함
   cron idle-watch.sh (재작성) ── 60분 유휴 → 텔레그램 알림 → self-stop
[Claude 모바일 앱] ── Anthropic ── EC2 claude 프로세스
```

### 3-2. systemd 템플릿 유닛 + tmux 래퍼

`ec2/claude-rc@.service`:
- `User=ubuntu`, `WorkingDirectory=/home/ubuntu/worktrees/%i`(ops는 `/home/ubuntu/gamer4info` — 래퍼가 `%i == ops`면 경로를 바꾼다), `EnvironmentFile=/home/ubuntu/.config/gamer4/secrets.env`(0600), `ExecStart=/home/ubuntu/bin/claude-rc-wrap.sh %i`, `ExecStop=tmux kill-session -t rc-%i`, `Restart=on-failure`, `RestartSec=10`.
- `claude-rc-wrap.sh <name>`: 기존 `claude-supervise.sh` 구조(유휴 카운터 리셋, credentials 가드, tmux 생성, 블로킹 루프) 재사용. 실행 명령:
  `claude remote-control --name "gamer4-<name>" --spawn same-dir --capacity 2 --permission-mode default --no-chrome`
  `CLAUDE_CODE_OAUTH_TOKEN`은 절대 export하지 않는다.
- 기존 `claude-telegram.service`·`start-claude-telegram.sh`는 **삭제**(disable 후). 리포에서도 제거하고 README·OPERATIONS 갱신.

### 3-3. 람다 v5 명령

| 명령 | 동작 | 응답 |
|---|---|---|
| `/start` | 기존. `ec2:StartInstances`. 세션은 systemd가 띄움 | "1분 뒤 앱 Code 탭에 `gamer4-ops`" |
| `/stop` | 기존 | |
| `/status` | EC2 상태 + `systemctl list-units 'claude-rc@*'` + `claude doctor` 요약(인증 OK 여부) | |
| `/sessions` | 유닛별 active 여부 + 워크트리 경로 + 브랜치 + 마지막 트랜스크립트 mtime | |
| `/new <name>` | SSM: `[ -d ~/worktrees/<name> ] \|\| git -C ~/gamer4info worktree add ~/worktrees/<name> -b <name> origin/main` → `cp ~/gamer4info/.env ~/worktrees/<name>/.env` → `sudo systemctl start claude-rc@<name>` | "`gamer4-<name>` 기동 중, 30초 뒤 앱에서 확인" |
| `/kill <name>` | SSM: `sudo systemctl stop claude-rc@<name>` (워크트리·브랜치 유지). `ops`는 거부 | |
| `/rm <name>` | (선택) `/kill` 후 `git worktree remove` — **미커밋 변경이 있으면 거부** | |

- `<name>` 검증: `^[a-z0-9-]{1,24}$`, `ops` 예약. SSM 스크립트는 `sudo -u ubuntu`로 실행, sudoers에 `systemctl start|stop claude-rc@*`만 NOPASSWD.
- API GW 30초 제한: `/new`는 `systemctl start`까지만 기다리고(≤5s) 세션 온라인 확인은 `/sessions`로. v3 포스트모템(SSM 원격 기동이 모든 실패 모드의 원인)과의 차이: 여기서 SSM은 **systemd 유닛을 켜는 것**뿐이고, 프로세스 수명은 systemd가 갖는다.

### 3-4. 세션 종료 의미 (요구 3)

- Remote Control 세션에 "종료"는 없다. 프로세스가 살아 있으면 온라인, 죽으면 몇 초 뒤 앱에서 오프라인.
- **명시 종료** `/kill <name>`: 유닛 정지. EC2 CLAUDE.md에 규칙 추가 — "세션을 닫기 전 커밋·푸시하고 OPERATIONS를 갱신한다".
- **EC2 정지**(`/stop` 또는 유휴): 전 세션 오프라인. 다음 부팅엔 `ops`만 자동 기동. 4시간 이내 재기동이면 서버 세션 복귀, 그 뒤엔 새 세션 — 연속성은 리포 문서(OPERATIONS §2·§5·§7)로만 담보(이미 확립된 규칙).
- **유휴 자동 종료** `ec2/idle-watch.sh` 재작성: tmux 화면 해시 폐기. 활동 신호 = ① `~/.claude/projects/*/*.jsonl` 최신 mtime, ② 기존 `~/.claude/jobs/*/state.json` non-terminal(신선도 30분), ③ `claude` 프로세스 누적 CPU 증가분. 셋 중 하나라도 최근 `IDLE_MINUTES`(60) 안에 움직이면 카운터 리셋. 종료 전 텔레그램 알림(기존 코드는 채널 플러그인 `.env`에서 토큰을 읽었으나 플러그인이 사라지므로 **boot-bot 토큰을 `secrets.env`에 두고 그것으로 발송**).

### 3-5. 권한·보안

- `~/.claude/settings.json`(user): `defaultMode: default`, `allow` = 읽기·진단(git status/log/diff, vercel ls/logs/metrics, aws logs/cloudformation describe, curl -s, yarn lint/verify), `ask` = OPERATIONS §1-3와 1:1(sam deploy, yarn deploy:lambda, vercel promote/redeploy/env/firewall/api, gh pr merge, sb_sql.py, aws lambda update-function-code), `deny` = `aws iam:*`, `Read(./.env)`, `Read(./**/.env)`. `remoteControlAtStartup: false`, `dialogExpiry: 0`.
- 운영 규칙: 폰 프롬프트에서 "항상 허용" 금지. 주기적으로 `permissions.allow` 감사.
- SG: SSH 22 인바운드 제거(SSM만). AWS 키 복사 금지 → 인스턴스 역할 `ec2-ssm-role`에 인라인 정책 추가(`iam/gamer4-operator-policy.json` 신설): cloudformation Describe*/CreateChangeSet/ExecuteChangeSet(stack `gamer4-sync`), lambda Get*/UpdateFunctionCode·Configuration(prefix `gamer4-sync-*`), logs Describe*/Filter*/Get*, s3 Get/Put(SAM 버킷 `aws-sam-cli-managed-default-*`), ec2 StopInstances(self, 기존), ssm 없음.
- `sb_sql.py`는 리포 `scripts/ops/`로 옮기며 키체인 대신 `SUPABASE_ACCESS_TOKEN` env를 읽고 `CERT_NONE`을 제거한다(gamer4info 쪽 작업).

### 3-6. 사용자 1회 작업 (요구 4) — SSM 세션 셸에서

1. `~/.claude/.env`에서 `CLAUDE_CODE_OAUTH_TOKEN` 제거 → `claude auth login`(claude.ai) → `claude doctor`.
2. `gh auth login` (또는 `GH_TOKEN` PAT를 `secrets.env`에).
3. `npx vercel login` + `cd ~/gamer4info && npx vercel link --scope byseops-projects` (또는 `VERCEL_TOKEN`).
4. Supabase 개인 토큰 → `secrets.env`의 `SUPABASE_ACCESS_TOKEN`(맥 키체인 값).
5. `~/gamer4info/.env`(REVALIDATE_SECRET 포함) + `infra/samconfig.toml` 붙여넣기.
6. 인스턴스 역할 IAM 정책 추가(콘솔 또는 `aws iam put-role-policy`, 승인 후 오퍼레이터가 파일 제공).
7. `cd ~/gamer4info && claude` → 신뢰 수락 → 종료. `claude remote-control --name gamer4-ops --spawn same-dir --capacity 2 --permission-mode default --no-chrome` → "Enable Remote Control? y" → QR을 폰으로 스캔 → 대화 확인 → Ctrl+C.
8. 폰 앱 `/config`: "Push when Claude decides", "Push when actions required" 켜기.
9. `sudo systemctl enable --now claude-rc@ops`, `sudo systemctl disable --now claude-telegram`.

### 3-7. 용량 선결 조건

- `df -h /`, `free -m`, 인스턴스 타입 확인. 24GB EBS면 워크트리 2개까지. 세션 3개 이상을 원하면 t3.large + EBS 40GB로 확장(사용자 결정).

## 4. 범위 밖 / 폴백

- 텔레그램 채널 플러그인(메인 봇) 자연어 대화는 폐기. 폴백이 필요하면 `--channels` 세션을 별도 유닛으로 되살릴 수 있다(설계 유지 안 함).
- 정기 점검 자동화는 클라우드 `/schedule` 세션으로(EC2 불필요) — 별도 항목.

## 5. 검증 (계획 단계에서 태스크화)

- 람다: `tests/test_lambda_function.py`에 `/new` 이름 검증·`ops` 예약·`/kill ops` 거부·SSM 스크립트 문자열 스냅샷 추가.
- idle-watch: 가짜 `~/.claude/projects` 트랜스크립트 mtime·jobs state로 리셋/카운트/종료 분기 셸 테스트(`bats` 또는 bash 단정문). **RED 먼저**.
- EC2 스모크: `/start` → 90초 내 앱에 `gamer4-ops` 온라인; `/new t1` → 앱에 `gamer4-t1`; `/kill t1` → 오프라인; 폰에서 `sam deploy` 요청 시 승인 프롬프트가 폰에 뜨고 거부하면 실행 안 됨; 60분 유휴 시 알림 후 정지(IDLE_MINUTES를 5로 낮춰 테스트).

# gamer4info 오퍼레이터 환경 → EC2 이전 타당성 보고서
## 주 설계: Claude Code **Remote Control** (모바일 앱에서 직접 조종) · 대안: 텔레그램 봇

조사일 2026-09-22 · 읽기 전용 조사(파일 수정·배포·AWS/Vercel/Supabase 상태 변경 없음)
근거는 항목마다 "증거:"로 명시. EC2에는 SSH/SSM 접속하지 않았고 AWS는 `describe-*` 읽기 호출만 했다.

---

## 1. 결론 한 줄

**Remote Control 방식이면 옮길 수 있고, 텔레그램 방식보다 확실히 낫다** — 퍼미션 승인이 휴대폰으로 그대로 전달되고(푸시 알림 포함), 서버 모드 하나로 워크트리별 다중 세션이 동시에 뜨며, diff 패널까지 앱에서 보인다. **단, 기존 EC2의 인증 방식(`CLAUDE_CODE_OAUTH_TOKEN`)으로는 Remote Control이 아예 안 되고**(§2-2, 치명적), t3.medium 4GiB·24GB 디스크로는 세션 2~3개가 상한이다. 권고는 **인스턴스 사이즈업 + `claude remote-control --spawn worktree` 서버 1개 + Manual 모드 + ask 규칙**(§7).

---

## 2. Remote Control 설계 (1순위)

증거: `claude --help`, `claude remote-control --help`(로컬 v2.1.278 실행), 공식 문서 `https://code.claude.com/docs/en/remote-control`, `https://code.claude.com/docs/en/permission-modes`.

### 2-1. 무엇인가 / 무엇이 좋아지는가

로컬(=EC2) `claude` 프로세스가 **아웃바운드 HTTPS만으로** Anthropic API에 등록하고 폴링한다. **인바운드 포트를 열지 않는다.** 휴대폰/브라우저는 그 세션을 들여다보는 창일 뿐이고, 코드 실행·파일 접근은 전부 EC2에서 일어난다.

텔레그램 대비 실질적 이득:
- **퍼미션 프롬프트가 휴대폰으로 전달된다.** 푸시 알림(`Push when actions required`)까지 온다 → §3에서 고민하던 "승인 게이트를 어떻게 만들 것인가"가 **제품 기본 기능으로 해결**된다. 훅·Lambda `/approve` 같은 자작 장치가 불필요.
- **파일·이미지 양방향.** 폰에서 사진·파일 첨부 가능, 세션 디렉터리가 git repo면 **연결된 기기에서 uncommitted diff 패널**을 본다. (텔레그램 채널 플러그인은 `reply`/`react`/`edit_message`뿐 — 긴 산출물 회수 경로가 없었다.)
- **서브에이전트·워크플로 진행 상황이 동기화**된다. superpowers SDD 워크플로(구현자/리뷰어 opus 에이전트)의 진행을 폰에서 본다.
- **네트워크 끊김 자동 복구.** 인터랙티브 세션은 끊긴 동안 메시지·퍼미션 프롬프트를 큐에 쌓았다가 복구 시 전달.
- 앱/웹에서 `/model`, `/effort`, `/compact`, `/context`, `/usage`, `/rename` 등 사용 가능. 반면 **`/plugin`·`/resume`은 로컬 터미널 전용**.

요구사항 (문서 "Requirements"):
- **Pro/Max/Team/Enterprise 구독 필수. API 키 불가.** (메모리상 사용자 계정은 claude.ai Pro → 충족)
- `ANTHROPIC_BASE_URL`이 `api.anthropic.com` 외를 가리키면 불가. Bedrock/Vertex/Foundry 불가.
- **`DISABLE_TELEMETRY` / `DO_NOT_TRACK` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` / `DISABLE_GROWTHBOOK` 중 하나라도 설정돼 있으면 불가** (기능 플래그 평가가 꺼져서). EC2 환경변수·settings.json `env` 블록 양쪽을 확인해야 한다.
- **워크스페이스 신뢰 다이얼로그**를 프로젝트 디렉터리에서 최소 1회 수락해야 한다. 홈 디렉터리에서는 신뢰가 저장되지 않으므로 **반드시 `~/gamer4info`에서 처음 실행**할 것.
- 서버 모드 첫 실행 시 `Enable Remote Control? (y/n)` **1회성 확인**이 뜬다 → systemd로 바로 띄우면 여기서 막힌다(§2-3).

### 2-2. 인증 — **가장 중요한 발견 (기존 EC2 설정과 정면 충돌)**

> 문서 원문: *"Remote Control requires a full-scope login token" — You're authenticated with a long-lived token from `claude setup-token` or the `CLAUDE_CODE_OAUTH_TOKEN` environment variable. **These tokens can only make model requests, so they can't establish Remote Control sessions.** Run `claude auth login` …*

즉:
- **현재 EC2의 인증 방식(`~/.claude/.env`의 `CLAUDE_CODE_OAUTH_TOKEN`)으로는 Remote Control을 쓸 수 없다.** 메모리 `telegram-ec2-claude.md`에 기록된 "만료된 `.credentials.json`이 env 토큰을 가린다"는 함정을 피하려고 도입한 바로 그 구조가, Remote Control에서는 **정반대로 작동**한다.
- Remote Control은 **`claude auth login`(claude.ai OAuth) → `~/.claude/.credentials.json`** 을 요구한다. 그런데 `claude-supervise.sh`에는 **만료된 `.credentials.json`을 `.expired`로 치우는 가드**가 들어 있다 → Remote Control 구성으로 바꾸면 **이 가드가 켜지는 순간 로그인이 사라진다.**
- **조치**: Remote Control 구성으로 갈 경우
  1. `~/.claude/.env`에서 `CLAUDE_CODE_OAUTH_TOKEN` **제거** (남아 있으면 "API-key auth가 우선한다"는 이유로 Remote Control이 거부됨 — 문서 트러블슈팅 명시).
  2. EC2에서 `claude auth login` 을 **1회 대화형으로** 수행(SSM 셸에서). 이후 `.credentials.json`이 자동 갱신된다.
  3. `claude-supervise.sh`의 credentials 가드 블록을 **비활성화하거나, "만료 + `.env`에 토큰이 있을 때만 치운다"는 조건을 유지**한다(현재 코드는 이미 `grep -q 'CLAUDE_CODE_OAUTH_TOKEN' "$ENV_FILE"` 조건이 붙어 있어, `.env`에서 토큰을 지우면 가드가 자동으로 무력화된다 — **운 좋게 안전한 구조다**).
  4. 진단은 `claude doctor` (개별 적격성 체크 항목별 결과 출력). `claude auth logout && claude auth login`으로 캐시된 entitlement 갱신.
- **함정 재발 방지**: `claude setup-token`은 여전히 `-p`/배치 용도로만 쓰고, Remote Control 세션과 섞지 말 것. 두 인증을 한 머신에서 공존시키면 어느 쪽이 이겼는지 증상만으로는 구분이 안 된다(기존 401 무한재발 사건과 같은 종류의 혼란).

증거: `remote-control` 문서 Requirements / Troubleshooting 절, pocket-claude `ec2/claude-supervise.sh` 원문, 메모리 `telegram-ec2-claude.md`.

### 2-3. TTY가 필요한가 / systemd로 띄울 수 있는가

- 문서 Limitations: *"**To keep a session running on a remote machine after you disconnect from SSH, start it inside `tmux` or `screen`.**"* → **tmux 권장이 공식 입장**이다. 기존 pocket-claude가 이미 `claude-supervise.sh` → tmux 래퍼 구조를 갖고 있으므로 **그대로 재활용**하면 된다.
- 서버 모드는 "프로세스가 터미널에 머물며" 세션 URL을 표시하고 스페이스바로 QR을 토글한다 → **대화형 UI를 전제**한다. systemd `Type=simple`로 TTY 없이 띄우면 동작할 가능성은 있으나 **확인하지 못했다**(§8 미지). tmux 안에서 돌리는 쪽이 안전하고, `/view` 같은 화면 확인 수단도 살아난다.
- **SSH 로그아웃 생존**: tmux 안이면 생존. systemd `Restart=on-failure`가 tmux 래퍼를 감시하는 기존 구조를 유지한다.
- **1회성 수락 2건**(워크스페이스 신뢰 + `Enable Remote Control?`)은 **systemd 이전에 대화형으로 끝내둬야 한다.**

### 2-4. 다중 세션 — "머신은 하나인데 세션은 여러 개"

**결론: 서버 모드 하나로 해결된다. 프로세스를 N개 띄울 필요가 없다.**

`claude remote-control --help` 실측:
```
--spawn <mode>     same-dir | worktree | session   (default: same-dir)
--capacity <N>     Max concurrent sessions in worktree or same-dir mode (default: 32)
--[no-]create-session-in-dir
--permission-mode <mode>
--name <name>
--remote-control-session-name-prefix <prefix>   (default: hostname)
-c/--continue, --session-id <id>
```
- **`--spawn worktree`**: 앱에서 새 세션을 만들 때마다 **Claude Code가 git worktree를 자동으로 파서** 그 안에 세션을 띄운다. git 저장소 필요. 런타임에 `w` 키로 `same-dir` ↔ `worktree` 토글.
- **`--capacity` 기본 32** — 동시 세션 수 상한은 제품상 넉넉하다. 실제 상한은 **RAM**이다(§2-5).
- **세션 이름**: `--name` > `/rename` > 대화 히스토리의 마지막 의미 있는 메시지 > 자동 생성 `<hostname>-graceful-unicorn`. `--remote-control-session-name-prefix`(env `CLAUDE_REMOTE_CONTROL_SESSION_NAME_PREFIX`, 기본 hostname)로 접두사를 바꾼다 → **`gamer4-ops`, `gamer4-feat` 식으로 구분** 가능.
- 앱에서는 **Code 탭 → 세션 목록**에 컴퓨터 아이콘 + 초록 점(온라인)으로 뜬다. 세션 URL 직접 열기 / QR 스캔 / 목록에서 이름으로 찾기 세 가지.
- 참고: **인터랙티브 모드(`claude --remote-control`)는 프로세스당 remote 세션 1개**다. 여러 개가 필요하면 서버 모드를 쓰라는 게 문서의 명시적 안내다. `remoteControlAtStartup: true`로 모든 세션 자동 연결도 가능하지만, 그러면 프로세스 = 세션이 되어 메모리가 선형으로 늘어난다.

**그래도 systemd 템플릿 유닛이 필요한 경우**(서버 1개로 안 묶고, 워크트리별로 수명·모델·퍼미션을 따로 주고 싶을 때):

```ini
# /etc/systemd/system/claude-rc@.service
# 사용: systemctl start claude-rc@ops  /  systemctl start claude-rc@feat-price
[Unit]
Description=Claude Code Remote Control (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/worktrees/%i
Environment=HOME=/home/ubuntu
Environment=CLAUDE_REMOTE_CONTROL_SESSION_NAME_PREFIX=gamer4
EnvironmentFile=/home/ubuntu/.config/gamer4/secrets.env     # 0600, §4-4 키 목록
ExecStart=/home/ubuntu/bin/claude-rc-wrap.sh %i
ExecStop=/usr/bin/tmux kill-session -t rc-%i
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```
```bash
#!/bin/bash
# /home/ubuntu/bin/claude-rc-wrap.sh  — tmux 래퍼 (systemd가 PID를 잃지 않도록 블로킹)
set -u
NAME="$1"; SESSION="rc-$NAME"; DIR="/home/ubuntu/worktrees/$NAME"
export NVM_DIR=/home/ubuntu/.nvm; . "$NVM_DIR/nvm.sh"
export PATH="/home/ubuntu/.local/bin:/home/ubuntu/.bun/bin:$PATH"
# CLAUDE_CODE_OAUTH_TOKEN 은 절대 export 하지 않는다 (§2-2)
tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -c "$DIR" \
  "claude remote-control --name 'gamer4-$NAME' --spawn same-dir --capacity 4 --permission-mode default"
while tmux has-session -t "$SESSION" 2>/dev/null; do sleep 10; done
exit 1
```

**폰에서 세션을 새로 여는 방법 두 가지**
1. **(권장) 서버 모드 + `--spawn worktree`**: 앱에서 새 세션을 만들면 워크트리가 자동 생성된다. 아무 명령도 필요 없다.
2. **컨트롤러 세션**: 상시 떠 있는 `gamer4-ops` 세션에게 폰으로 "feat-x 세션 열어줘"라고 말하면, 그 세션이 `git worktree add ../worktrees/feat-x -b feat/x && systemctl --user start claude-rc@feat-x`를 실행한다. 단 `systemctl`은 root 권한이 필요하므로 **`systemd --user` 유닛으로 만들거나**(`loginctl enable-linger ubuntu` 필요), sudoers에 해당 명령만 NOPASSWD로 좁혀 허용한다. **`sudo systemctl` 전체를 허용하지 말 것.**

### 2-5. 세션당 자원 — 실측 (이 맥에서 `ps`)

| 프로세스 | RSS | 비고 |
|---|---|---|
| 인터랙티브 `claude` (거의 유휴, 7일 구동) | **121 MB** | pid 19971 |
| 인터랙티브 `claude` (gamer4info 오퍼레이터 세션, 7일, 컨텍스트 포화) | **518 MB**, CPU 9.6% | pid 45273 — **실사용 상한 표본** |
| `claude daemon run` (supervisor, 머신당 1개) | 69 MB | |
| `claude bg-pty-host` / `bg-spare` | 23~30 MB 각 | 백그라운드 에이전트용 |
| MCP 서버 (`npx @modelcontextprotocol/server-github`) | 4 MB | 세션당 1개씩 더 붙음 |

증거: `ps -eo rss,vsz,pid,etime,%cpu,comm -p 19971 -p 45273`, `ps -eo rss,command | grep claude`.

**사이징 (세션당 평균 350 MB / 무거운 세션 600 MB 가정, OS+데몬 ~600 MB)**

| 인스턴스 | vCPU / RAM | 현실적 동시 세션 | `yarn build` 동시 수행 | 시간당(서울, 온디맨드, ±10%) |
|---|---|---|---|---|
| **t3.medium (현재)** | 2 / 4 GiB | **2 (무거운 것 1 + 가벼운 것 1)** | 불가에 가까움 | ~$0.052 |
| t3.large | 2 / 8 GiB | 4~5 | 1개는 가능 | ~$0.104 |
| t3.xlarge | 4 / 16 GiB | 8+ | 여유 | ~$0.208 |
| t4g.large (Graviton, 새 인스턴스 필요) | 2 / 8 GiB | 4~5 | 1개 | ~$0.084 |

**t3는 버스터블이다** — baseline CPU 크레딧을 넘기면 스로틀되거나 unlimited 모드에서 추가 과금된다. 세션 여러 개가 동시에 툴을 돌리면(특히 `yarn install`, `sam build`, `next build`) 크레딧이 빠르게 마른다. 세션을 3개 이상 상시로 둘 거면 **t3.large 이상 + `unlimited` 모드 비용을 감수**하거나 논버스터블(m7i-flex 등)을 검토한다. 정확한 시간당 요금은 공식 가격표를 확인하지 못했다(§8).

**디스크가 더 빡빡하다.** 맥 기준 워크트리 1개당 node_modules 660~770 MB, 리포 전체 3.1 GB. EC2 EBS는 **24 GB gp3**(Ubuntu·claude·bun·SAM 빌드 캐시 포함). → **워크트리 2~3개가 물리적 상한.** 4개 이상 세션을 원하면 볼륨 확장이 선행 조건이다.

### 2-6. 서브에이전트 vs 다중 세션 — 권고

한 세션 안의 서브에이전트는 **이미 병렬로 돈다**(Agent 도구 다중 호출, `claude --bg`). gamer4info의 실제 워크플로(OPERATIONS §1-4, SDD)도 "세션 1개 + opus 하위 에이전트 N개" 구조다. 그리고 문서상 **서브에이전트·워크플로 진행 상황은 연결된 기기에 그대로 동기화**된다. 즉 **병렬성 때문에 세션을 늘릴 이유는 거의 없다.**

세션을 나눠야 하는 진짜 이유는 두 가지뿐이다:
- **컨텍스트 분리** — 운영 모니터링 대화와 기능 개발 대화가 서로를 오염시키지 않게. (운영자 메모리의 "세션은 주제(PR) 단위" 규칙과 정확히 일치)
- **워크트리 분리** — 서로 다른 브랜치에서 동시에 편집.

**권고 구성**:
- **상시 1개: `gamer4-ops`** — `~/gamer4info`(main 체크아웃), 장수 세션. 메트릭·로그·회차 검증·§7 운영 로그·승인 대기 처리. 컨텍스트가 차면 `/compact`.
- **온디맨드 1~2개: `gamer4-<feature>`** — 워크트리. PR이 머지되면 세션 종료 + `git worktree remove`.
- 총 3개를 넘기지 않는다. t3.medium이면 2개.

### 2-7. 퍼미션 모드 — 아무도 터미널을 안 보는 머신에서

문서 `permission-modes` 실측 요약:

| 모드 | 프롬프트 없이 되는 것 | EC2 적합성 |
|---|---|---|
| `default`(=Manual) | 읽기만 | **권장 기본값** |
| `acceptEdits` | 읽기 + 파일 편집 + mkdir/mv/cp 류 | 개발 세션에 적합 |
| `plan` | 읽기 + (auto 가능 시) 분류기 승인 명령 | 조사용 |
| `auto` | **전부**, 백그라운드 분류기가 대신 검토 | Pro/Max 기본값. 편하지만 **프로덕션 조작까지 분류기가 통과시킬 수 있음** |
| `dontAsk` | 읽기 + 사전승인 도구만, 나머지는 **거부** | 자동화/CI용. 사람이 승인할 여지가 없어 오퍼레이터엔 부적합 |
| `bypassPermissions` | 전부 | **금지** — 격리된 컨테이너 전용 |

**핵심 메커니즘: `permissions.ask` 규칙은 `auto`·`bypassPermissions`를 포함한 모든 모드에서 자동 승인되지 않는다.** (문서 "Actions no mode auto-approves" 첫 항목) → **모드를 무엇으로 두든 프로덕션 조작만은 반드시 물어보게 만들 수 있다.** 이게 텔레그램 설계에서 자작 훅으로 만들려던 승인 게이트의 정식 대체물이다.

권장 `~/.claude/settings.json`(EC2):
```jsonc
{
  "permissions": {
    "defaultMode": "default",
    "allow": [
      "Read", "Glob", "Grep",
      "Bash(git status:*)", "Bash(git log:*)", "Bash(git diff:*)",
      "Bash(npx vercel ls:*)", "Bash(npx vercel logs:*)", "Bash(npx vercel metrics:*)",
      "Bash(sam logs:*)",
      "Bash(aws logs:*)", "Bash(aws cloudformation describe-stacks:*)",
      "Bash(curl -s:*)", "Bash(yarn lint:*)", "Bash(yarn verify:*)"
    ],
    "ask": [
      "Bash(sam deploy:*)", "Bash(yarn deploy:lambda:*)",
      "Bash(npx vercel promote:*)", "Bash(npx vercel redeploy:*)",
      "Bash(npx vercel env:*)", "Bash(npx vercel firewall:*)", "Bash(npx vercel api:*)",
      "Bash(gh pr merge:*)",
      "Bash(python3:*sb_sql.py*)",
      "Bash(aws lambda update-function-code:*)", "Bash(aws ec2 terminate-instances:*)"
    ],
    "deny": [
      "Bash(aws iam:*)",
      "Read(./.env)", "Read(./**/.env)"
    ]
  },
  "remoteControlAtStartup": false,
  "dialogExpiry": 0
}
```
- `ask` 목록이 **OPERATIONS §1-3의 "운영자 승인 후 실행" 항목과 1:1로 대응**하게 유지한다. 규칙이 문서와 어긋나기 시작하면 게이트는 장식이다.
- **"always allow"의 위험**: 폰에서 프롬프트에 "항상 허용"을 누르면 **settings에 allow 규칙이 영구 기록**되어 그 게이트가 사라진다. 프로덕션 조작 프롬프트에서는 **절대 누르지 않는다**는 걸 운영 규칙으로 못박고, 주기적으로 `permissions.allow`를 감사한다.
- 퍼미션 프롬프트와 `AskUserQuestion`은 **답할 때까지 열려 있다**(만료 없음). 그 외 다이얼로그는 기본 5분 후 무동작 기본값으로 닫히므로 `dialogExpiry`로 조정한다.
- **주의**: `.claude/settings.json`·`settings.local.json`(프로젝트/로컬)에 `"auto"`나 `"bypassPermissions"`를 적으면 **무시된다**. 모드는 `~/.claude/settings.json`(user) 또는 CLI 플래그로만 확실히 먹는다.

### 2-8. Lambda로 EC2 켜기 / 끄기

기존 pocket-claude의 boot-manager Lambda를 **거의 그대로 재사용**한다. 역할이 더 단순해진다 — 텔레그램 대화는 더 이상 Lambda를 거치지 않고, **Lambda는 전원 스위치와 상태창만** 남는다.

```
[Telegram boot bot]  /start  /stop  /status
   └─ API GW ─▶ Lambda(ec2-boot-manager)
                 ├─ ec2:StartInstances   ← 부팅되면 systemd가 claude-rc 유닛을 자동 기동
                 ├─ ec2:StopInstances
                 └─ ssm:SendCommand      ← systemctl is-active / claude doctor 결과 조회
[Claude 모바일 앱] ──HTTPS──▶ Anthropic API ──▶ EC2의 claude 프로세스 (인바운드 포트 없음)
```
- 부팅 시 자동 기동: `systemctl enable claude-rc@ops`(+ 필요한 워크트리). **Lambda는 세션을 띄우지 않는다** — pocket-claude가 v3에서 SSM으로 원격 기동했다가 모든 실패 모드가 그 결정으로 수렴했다는 포스트모템이 이미 리포에 있다. 그 교훈을 유지한다.
- **정지**: ① 기존 `idle-watch.sh` cron(5분 간격, 60분 유휴 시 self-stop) 유지, ② 폰에서 ops 세션에 "이제 꺼줘" → 세션이 `aws ec2 stop-instances --instance-ids $(자기 IID)` 실행(인스턴스 역할에 자기 ARN 한정 `ec2:StopInstances`가 이미 있음), ③ 텔레그램 `/stop`.
- **`idle-watch.sh`는 반드시 수정해야 한다.** 현재는 **tmux 화면 md5 해시**로 유휴를 판정하는데, Remote Control 세션은 **폰에서 대화 중이어도 EC2 터미널 화면이 안 바뀔 수 있다** → 작업 중에 인스턴스가 꺼진다. 대체 신호:
  - `~/.claude/projects/*/*.jsonl`(대화 트랜스크립트)의 최신 mtime,
  - 기존 `~/.claude/jobs/*/state.json` non-terminal 체크(이미 구현돼 있음),
  - `claude` 프로세스의 누적 CPU 증가분.
  세 가지 중 하나라도 최근 N분 내 움직였으면 카운터를 리셋한다. **이 수정 없이 Remote Control로 전환하면 작업 중 종료가 반복된다.**
- 비용: Lambda·API GW·EventBridge는 프리티어 범위. EC2 요금은 §6.

### 2-9. EC2가 멈추면 / 세션 재개

- 인스턴스 정지 = `claude` 프로세스 종료 → 앱에서 몇 초 내 **offline**으로 표시. 대화는 사라지지 않는다.
- **서버 모드 재개**: 같은 디렉터리에서 `claude remote-control` → 그 서버가 서빙하던 **모든 세션이 복귀**. `--continue`는 마지막 1개, `--session-id <id>`는 지정 1개. **정지 후 약 4시간 이내에만 유효**하고, 그 뒤엔 새 세션이 된다. (`--no-create-session-in-dir`로 띄웠던 서버는 정지 시 세션을 아카이브하므로 복귀 대상이 없다 — 쓰지 말 것.)
- **인터랙티브 모드(`claude --remote-control`) 재개**: `claude --continue` / `claude --resume`으로 대화를 되살리면 재연결 기록에 따라 Remote Control도 붙는다.
- **4시간 규칙의 운영적 함의**: 유휴 자동종료(60분)와 조합하면, 밤에 꺼진 인스턴스를 아침에 켰을 때 세션은 **새로 만들어진다.** 따라서 **세션 간 연속성은 리포 문서(OPERATIONS §2·§5·§7)에만 의존해야 한다** — 이미 확립된 규칙이라 추가 부담은 없다.
- 네트워크 장애: 서버 모드는 약 10분 뒤 프로세스가 종료된다(→ systemd `Restart=on-failure`가 되살린다). 인터랙티브 모드는 계속 재시도한다.

### 2-10. 보안 관점

- **인바운드 포트 0.** SG에서 SSH 22를 닫아도 Remote Control은 동작한다(SSM만 있으면 됨) → §6의 권고와 충돌하지 않는다.
- 전송은 TLS, 자격증명은 목적별 단수명 토큰이 여러 개. **단, 세션 트랜스크립트(내 메시지·Claude 응답·툴 활동)가 Anthropic 서버에 저장된다.** 기기 간 동기화·재연결에 필요해서다. gamer4info는 개인 비수익 프로젝트라 문제없을 가능성이 높지만, **`.env` 값이나 service_role 키를 세션에서 출력하면 그게 저장된다.** 위 settings의 `deny: Read(./.env)`가 그래서 들어갔다.
- 완전 비활성화는 `disableRemoteControl` 설정. Team/Enterprise의 Trusted Devices(생체 인증 기반)는 개인 Pro 플랜에는 해당 없음.

---

## 3. 대안(폴백): 텔레그램 / pocket-claude

기존 구조는 이미 배포돼 검증됐으므로 **폴백으로 유지할 가치가 있다.**

| 항목 | Remote Control | 텔레그램 채널 플러그인 (현행) |
|---|---|---|
| 기동 | `claude remote-control` (tmux + systemd) | `claude --dangerously-skip-permissions --channels plugin:telegram@…` |
| 인증 | **`claude auth login` 전용**, `CLAUDE_CODE_OAUTH_TOKEN` 불가 | `CLAUDE_CODE_OAUTH_TOKEN` |
| 퍼미션 승인 | **폰으로 전달 + 푸시** | **없음**(bypass) → 자작 게이트 필요 |
| 다중 세션 | 서버 모드 `--spawn worktree`, capacity 32 | `claude --bg` + `claude agents`(자연어 관리) |
| 파일·이미지 | 양방향 + diff 패널 | `reply`/`react`/`edit_message`뿐 (파일 전송 미확인) |
| EC2 꺼짐 시 | 4시간 내 `remote-control` 재개 | tmux 세션 재생성 |
| 전원 제어 | boot-manager Lambda (그대로 재사용) | 동일 |
| 장점 | 기능 전반이 앞섬 | **인스턴스가 꺼져 있어도 봇이 응답**(Lambda가 밖에 있음) |

→ **`/start` `/stop` `/status`용 boot-manager 봇은 Remote Control 구성에서도 그대로 남긴다.** 인스턴스가 꺼져 있으면 Claude 앱에서는 아무것도 할 수 없고, 켜 줄 수단이 바깥에 있어야 하기 때문이다. 메인 클로드 봇(채널 플러그인)만 Remote Control로 대체한다.

---

## 4. 현재 환경 인벤토리

### 4-1. 툴체인

| 항목 | 맥에서의 위치/버전 | EC2에서 대체 방법 | 난이도 |
|---|---|---|---|
| Node | v22.18.0 (`~/.nvm/versions/node/v22.18.0/bin/node`) | EC2에 nvm 이미 있음(`NVM_DIR=/home/ubuntu/.nvm`). 버전만 맞춤 | 쉬움 |
| yarn | 1.22.22 (`/opt/homebrew/bin/yarn`) | `npm i -g yarn@1.22.22` | 쉬움 |
| python3 | 3.11.15 (homebrew) + `/usr/bin/python3` | Ubuntu 기본. 진단 스크립트는 표준 라이브러리만 사용 | 쉬움 |
| AWS CLI | v2.34.23 | zip 설치. **`ca_bundle` 설정은 복사 금지**(§5) | 쉬움 |
| SAM CLI | 1.159.1 | zip 설치. `template.yaml`이 `BuildMethod: esbuild`라 **Docker 불필요** | 보통 |
| gh CLI | 2.89.0, `gho_…`(keyring), scopes `gist, read:org, repo, workflow` | Linux엔 데스크톱 keyring 없음 → **새 PAT + `GH_TOKEN`** | 보통 |
| Vercel CLI | `npx vercel`, 인증 `~/Library/Application Support/com.vercel.cli/auth.json`(`token`/`refreshToken`/`expiresAt`), `config.json` → `currentTeam=team_6jJIrJ9KlhfGJgVrver5OsS5` | `vercel login`은 브라우저 device flow → **Access Token 발급 → `VERCEL_TOKEN` + `--scope byseops-projects`** | 보통 |
| Vercel 프로젝트 링크 | 리포 `.vercel/project.json` (`prj_FHByt16uk9GpEuBY5LubL2Elt8oe` / `gamer4-info`) | gitignore 대상 → 복사 또는 `vercel link` | 쉬움 |
| Claude Code | **2.1.278** (`~/.local/bin/claude`) | EC2는 메모리 기준 **2.1.146** → **필수 업그레이드**. Remote Control의 `--continue`/`--session-id`는 v2.1.200+, 크래시 복구는 v2.1.238+, Manual 별칭은 v2.1.200+ | 쉬움 |
| git | 2.50.1 | 기본 제공 | 쉬움 |
| tmux | **맥엔 없음** | EC2엔 필수(§2-3). 이미 사용 중 | — |
| bun | 1.3.11 | EC2에 `BUN_INSTALL=/home/ubuntu/.bun` 존재. Remote Control 구성에선 텔레그램 MCP 서버용이라 필수는 아님 | — |

증거: `node -v`/`yarn -v`/`aws --version`/`sam --version`/`gh --version`/`gh auth status`/`python3 --version`/`claude --version`/`which -a`, `ls ~/Library/Application Support/com.vercel.cli`, `cat .vercel/project.json`, `grep BuildMethod infra/template.yaml`, pocket-claude `ec2/start-claude-telegram.sh`.

### 4-2. Claude Code 설정·플러그인·메모리

| 항목 | 맥에서의 위치 | EC2에서 대체 방법 | 난이도 |
|---|---|---|---|
| 전역 지침 | `~/.claude/CLAUDE.md` | EC2엔 이미 다른 내용(99줄, "Background Agent Management")이 있다 → **머지, 덮어쓰기 금지**. 백업 `~/.claude/CLAUDE.v1.bak.md` | 보통 |
| settings | `~/.claude/settings.json` — `model: claude-fable-5-1`, `permissions.defaultMode: "auto"`, hooks 6종 전부 `/Users/youngsbae/.config/iterm2/cc-status` | **hooks 전부 제거**(iTerm2 전용). `defaultMode`는 §2-7대로 `default`로 변경 권장 | 쉬움 |
| 자동 메모리 | `~/.claude/projects/-Users-youngsbae/memory/` (8개) | **디렉터리명이 cwd 슬러그** → EC2에선 `-home-ubuntu` 등으로 바뀐다. 복사만 하면 안 읽힘, **rename 필요** | 보통(함정) |
| 플러그인 | `~/.claude/plugins/cache/claude-plugins-official/` — superpowers 6.3.0, code-review, github, claude-md-management, commit-commands, frontend-design, context7, figma, vercel, skill-creator | `claude plugin install` 재실행. **`/plugin`은 로컬 터미널 전용 명령**이라 폰에서 설치 불가 → 사전 설치 필수 | 쉬움 |
| MCP 서버 | `~/.claude.json`: top-level `mcpServers` 비어 있음, **project scope `/Users/youngsbae` → `["github"]`** | 홈 경로가 바뀌므로 `/home/ubuntu` 스코프로 재등록. 폰에서 `/mcp reconnect|enable|disable`은 가능 | 쉬움 |
| claude-in-chrome | 브라우저 확장 기반 | **EC2 불가**(§5). `claude remote-control --no-chrome`으로 명시적으로 꺼둘 것 | 불가 |
| vercel / figma MCP | 플러그인 MCP, OAuth `authenticate` | 브라우저 필요 → 헤드리스에선 사실상 불가. **vercel은 CLI + `VERCEL_TOKEN`으로 대체**(실제 운영 로그도 전부 CLI 사용) | 보통 |

증거: `cat ~/.claude/settings.json`, `~/.claude.json` 파싱, `ls ~/.claude/plugins/cache/…`, `cat installed_plugins.json`, `ls ~/.claude/projects/-Users-youngsbae/memory/`, 메모리 `telegram-ec2-claude.md`.

### 4-3. 리포·작업 산출물

| 항목 | 맥에서의 위치 | EC2에서 | 난이도 |
|---|---|---|---|
| 리포 | `.../myproject/gamer4info` 총 **3.1 GB**, node_modules 767 MB | `git clone` + `yarn install` | 쉬움 |
| 워크트리 5개 | `.claude/worktrees/{feat-latest-stats, feat-price-consistency, feat+error-pages, ops-log-4, ops-log-5}`, 각 node_modules 660~770 MB, 합 **2.2 GB** | **24 GB EBS에 2~3개가 상한**(§2-5) | 보통 |
| 세션 스크래치패드 | `~/.claude/jobs/<session>/tmp/` — 실제 운영에 쓰인 임시 스크립트·덤프 **136개** | 경로 동일. 인스턴스 정지로 세션이 끊기므로 **연속성은 리포 문서에만 의존** | 쉬움 |
| 진단 커맨드 | OPERATIONS §6의 `npx vercel metrics/logs/ls --scope byseops-projects` | 토큰만 있으면 동일 | 쉬움 |
| 람다 배포 | `yarn deploy:lambda` = `.env` source → `sam build && sam deploy --parameter-overrides` | 동일. EC2는 **x86_64**지만 타깃은 arm64 — esbuild는 JS 번들만 하므로 문제없음 | 보통 |
| 마이그레이션 | `python3 sb_sql.py <file>`(운영자 직접 실행, keychain 토큰) | keychain 없음 → env 토큰으로 재작성(§5) | 보통 |

증거: `du -sh`, `git worktree list`, `ls -la ~/.claude/jobs/a8f336f1/tmp/`, `cat infra/README.md`, `cat package.json`, `aws ec2 describe-volumes`.

### 4-4. 비밀정보 — 어디에 무엇이 있는가 (키 이름만)

| 비밀 | 현재 위치 | EC2에서 | 비고 |
|---|---|---|---|
| `ITAD_CLIENT_ID` / `ITAD_CLIENT_SECRET` / `ITAD_API_KEY` | 리포 `.env` (gitignore `.env*`) | 동일 | — |
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | 리포 `.env` | 동일 | read-only |
| `SUPABASE_URL` / **`SUPABASE_SERVICE_KEY`** | 리포 `.env` | 동일 | **DB 전체 쓰기. EC2에 놓이는 순간 위험 증가**(§6) |
| `REVALIDATE_SECRET` | **워크트리 `.env`에만 존재**, 메인 체크아웃 `.env`엔 없음 | 이전 시 누락 주의 | 드리프트 발견 — `yarn deploy:lambda`가 요구 |
| AWS 액세스 키 | `~/.aws/credentials` `[default]` → **`arn:aws:iam::AWS_ACCOUNT_ID:user/byseop-admin`** | **키 복사 금지 권장** → 인스턴스 역할에 최소권한(§6) | admin 추정 |
| AWS 리전/CA | `~/.aws/config`: `region=ap-northeast-2`, **`ca_bundle=/Users/youngsbae/.aws/combined-ca.pem`** | region만, **ca_bundle 줄 제거**(§5) | — |
| GitHub 토큰 | macOS keyring, `gho_…` | 새 PAT → `GH_TOKEN` | — |
| Vercel 토큰 | `~/Library/Application Support/com.vercel.cli/auth.json` | 대시보드 Access Token → `VERCEL_TOKEN` | — |
| Supabase PAT | **macOS Keychain** genp `svce="Supabase CLI"`, `acct="supabase"`, `go-keyring-base64:` 접두 + base64(`sbp_…`), 수정 2026-09-21 | `SUPABASE_ACCESS_TOKEN` env 또는 `~/.supabase/token`(0600) | §5에 스크립트 수정안 |
| Claude 인증 | `~/.claude/.credentials.json` (OAuth) | **`claude auth login` 필수**, `CLAUDE_CODE_OAUTH_TOKEN` 금지(§2-2) | 구조 변경 |
| `infra/samconfig.toml` | gitignored이나 **example과 동일 — 비밀 없음**(stack_name/region/capabilities만). 비밀은 CLI `--parameter-overrides`로만 주입 | 그대로 복사 가능 | 좋은 설계 |
| 텔레그램 봇 토큰 | EC2 `~/.claude/channels/telegram/.env`, `access.json` | boot-manager 봇용으로 유지 | — |

증거: `cut -d= -f1 .env`(값 미출력), `security find-generic-password -s "Supabase CLI"`(메타데이터만, `-w` 미사용), `cat ~/.aws/config`, credentials는 키 이름만 grep, `gh auth status`, auth.json은 키 목록만, `cat infra/samconfig.toml`, `aws sts get-caller-identity`.

### 4-5. 기존 EC2 (메모리 + read-only 조회)

- `INSTANCE_ID` `claude-code-machine` — **t3.medium / x86_64 / Ubuntu / 현재 stopped**, 인스턴스 프로파일 `ec2-ssm-role`, EBS **24 GB gp3**, 마지막 launch 2026-08-10. EIP 없음. 계정 AWS_ACCOUNT_ID / ap-northeast-2 — **맥의 AWS 자격증명과 같은 계정**이고 `gamer4-sync` 람다도 여기 산다.
- 기동 체인: `systemd claude-telegram.service (Restart=always, User=ubuntu)` → `claude-supervise.sh` → `tmux new-session -d -s claude` → `start-claude-telegram.sh` → `exec claude --dangerously-skip-permissions --channels …`. **작업 디렉터리는 `/home/ubuntu`** — Remote Control은 프로젝트 디렉터리에서 시작해야 하므로 **반드시 바꿔야 한다**(홈에서는 워크스페이스 신뢰가 저장되지 않음).
- supervisor의 두 가드(만료 credentials 치우기 / 낡은 데몬 정리)와 `idle-watch.sh`(cron 5분, 60분 유휴 self-stop, `~/.claude/jobs` 체크)는 §2-2·§2-8에서 각각 손봐야 한다.
- boot-manager Lambda: API GW webhook, `ALLOWED_CHAT_ID` 화이트리스트, `/start` `/stop` `/status` `/view`, 권한은 `ec2:Start/StopInstances` + `ssm:SendCommand`뿐.

증거: `gh api repos/byseop/pocket-claude/contents/...`로 README, `claude-telegram.service`, `claude-supervise.sh`, `start-claude-telegram.sh`, `idle-watch.sh`, `install.sh` 원문 + `aws ec2 describe-instances/describe-volumes` + 메모리.

---

## 5. 옮기면 안 되거나 다르게 해야 하는 것

1. **`~/.aws/config`의 `ca_bundle` 줄 복사 금지.** 사내 SWG(`CN=swg.gmarket.com`)가 `*.api.aws` TLS를 인터셉트하는 **맥 네트워크 전용** 우회다. EC2엔 SWG가 없어 오히려 검증이 깨진다. `region`/`output`만 옮긴다.
2. **`CLAUDE_CODE_OAUTH_TOKEN` 은 Remote Control 구성에서 반드시 제거.** §2-2. 남아 있으면 "API-key auth를 쓰고 있어서 Remote Control 불가"로 거부된다.
3. **`sb_sql.py`를 그대로 옮기면 안 된다.** ① keychain 읽기(`security find-generic-password … -w` + `go-keyring-base64:` 언랩)를 `os.environ["SUPABASE_ACCESS_TOKEN"]`으로 교체, ② **`ssl.CERT_NONE` 제거** — SWG 때문에 들어간 것으로 보이며 EC2에선 불필요하고 MITM에 무방비다.
4. **claude-in-chrome MCP는 EC2 불가.** 데스크톱 Chrome 확장에 붙는 구조다. 대안: `curl -A "<UA>" -D -`(현 운영 로그의 sitemap/robots/firewall 검증이 실제로 이 방식), GSC는 Search Console API. **시각 확인이 필요한 작업은 맥에 남긴다.** Remote Control 서버는 `--no-chrome`으로 명시.
5. **워크트리 경로·개수.** 24 GB EBS에 워크트리 2~3개가 상한(§2-5). `--spawn worktree`를 쓰면 세션이 늘 때마다 워크트리가 자동 생성되므로 **`--capacity`를 낮게(2~4) 잡아 디스크 폭주를 막는다.** 세션 종료 시 `git worktree remove` 또는 `claude rm`.
6. **git 안전성.** 워크트리 분리는 필수지만 **git stash 스택은 워크트리 전체가 공유**한다 — 여러 세션이 동시에 `git stash`/`git stash pop`을 하면 남의 작업을 꺼내간다. **EC2 CLAUDE.md에 "bare `git stash` 금지, 임시 WIP 커밋 사용, 부득이하면 `git stash push -u -m "<tag>"` + `apply <sha>`" 규칙을 명시**한다(이미 이 세션의 하니스가 강제하는 규칙과 동일). 같은 브랜치를 두 워크트리에 체크아웃하는 것도 git이 거부한다. `.git/index.lock` 경합을 피하려면 세션끼리 같은 디렉터리를 공유하지 않는다(= `--spawn same-dir`를 여러 개발 세션에 쓰지 않는다).
7. **`~/.claude/projects/-Users-youngsbae/memory/` 를 그대로 복사하면 안 읽힌다.** 디렉터리명이 cwd 슬러그다 → EC2 홈 경로에 맞춰 rename.
8. **`~/.claude/settings.json`의 hooks 6종은 iTerm2 전용** → 제거. 남길 것은 `model`과 `permissions`.
9. **EC2의 기존 `~/.claude/CLAUDE.md`(99줄)를 덮어쓰지 말 것.** 맥 전역본과 머지한다.
10. **`idle-watch.sh`의 tmux 화면 해시 판정은 Remote Control에 맞지 않는다**(§2-8). 수정 없이 전환하면 작업 중 종료가 반복된다.
11. **git 커밋 작성자.** EC2에서 gamer4info 커밋을 만들 거면 `git config user.name/email` 명시. `no-co-authored-by` 규칙도 EC2 CLAUDE.md에 포함.

---

## 6. 단계별 마이그레이션 절차

전제: 모든 `<…>`는 자리표시자. 접속은 SSH가 아니라 SSM.

### 0단계 — 접속
```bash
# 텔레그램 boot 봇 /start 또는
aws ec2 start-instances --region ap-northeast-2 --instance-ids INSTANCE_ID
aws ssm start-session --region ap-northeast-2 --target INSTANCE_ID
sudo -u ubuntu -i
```

### 1단계 — 용량 점검 (선결 조건)
```bash
df -h / ; free -m ; nproc
```
세션 3개 이상 또는 `yarn build`를 원하면 **인스턴스 타입·볼륨 상향이 선행**(승인 필요, 비용 발생):
```bash
aws ec2 modify-volume --region ap-northeast-2 --volume-id vol-0129091e6fa816826 --size 40
# 인스턴스에서: sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1
aws ec2 stop-instances  --region ap-northeast-2 --instance-ids INSTANCE_ID
aws ec2 modify-instance-attribute --region ap-northeast-2 \
  --instance-id INSTANCE_ID --instance-type '{"Value":"t3.large"}'
aws ec2 start-instances --region ap-northeast-2 --instance-ids INSTANCE_ID
```

### 2단계 — 툴체인
```bash
claude install latest           # 2.1.146 → 최신 (Remote Control 기능 요구 버전 충족)
npm i -g yarn@1.22.22
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install --update
curl -fsSL "https://github.com/aws/aws-sam-cli/releases/latest/download/aws-sam-cli-linux-x86_64.zip" -o /tmp/sam.zip
unzip -q /tmp/sam.zip -d /tmp/sam && sudo /tmp/sam/install --update
sudo apt-get update && sudo apt-get install -y gh tmux
mkdir -p ~/.aws && printf '[default]\nregion = ap-northeast-2\noutput = json\n' > ~/.aws/config
#   ↑ ca_bundle 줄 없음 — 의도된 것 (§5-1)
```

### 3단계 — 인증 (Remote Control 핵심)
```bash
# 3-1. 기존 모델 전용 토큰 제거 (§2-2)
grep -v CLAUDE_CODE_OAUTH_TOKEN ~/.claude/.env > /tmp/e && mv /tmp/e ~/.claude/.env
env | grep -E 'ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_BASE_URL|DISABLE_TELEMETRY|DO_NOT_TRACK|DISABLE_GROWTHBOOK|CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'
#   ↑ 하나라도 잡히면 제거. settings.json 의 env 블록도 확인

# 3-2. claude.ai 로그인 (대화형 1회)
claude auth login          # claude.ai 옵션 선택
claude doctor              # Remote Control 적격성 체크 항목별 확인

# 3-3. 나머지 비밀
umask 077
mkdir -p ~/.config/gamer4
cat > ~/.config/gamer4/secrets.env <<'EOF'
GH_TOKEN=<github_pat_...>
VERCEL_TOKEN=<vercel access token>
SUPABASE_ACCESS_TOKEN=<sbp_...>
EOF
chmod 600 ~/.config/gamer4/secrets.env
# AWS: 키 복사 대신 인스턴스 역할 권장(§7). 검증:
aws sts get-caller-identity
```

### 4단계 — 리포
```bash
mkdir -p ~/ && cd ~ && gh repo clone byseop/gamer4info && cd gamer4info
yarn install
# .env 전달 (git에 없음). 키 목록 §4-4 — REVALIDATE_SECRET 누락 주의
cp infra/samconfig.toml.example infra/samconfig.toml
mkdir -p .vercel   # project.json 복사 또는 `npx vercel link --scope byseops-projects`
aws cloudformation describe-stacks --stack-name gamer4-sync --region ap-northeast-2 \
  --query 'Stacks[0].StackStatus'
```

### 5단계 — Claude Code 설정
```bash
mkdir -p ~/.claude/projects/-home-ubuntu/memory     # 슬러그 확인 후 정확한 이름으로 (§5-7)
# 맥의 memory/*.md 복사

claude plugin install superpowers@claude-plugins-official
claude plugin install context7@claude-plugins-official
claude plugin install commit-commands@claude-plugins-official
claude plugin install code-review@claude-plugins-official

# ~/.claude/settings.json : §2-7 의 permissions 블록. iTerm2 hooks 제외.
# ~/.claude/CLAUDE.md : EC2 기존 99줄 + 맥 전역본 머지 (+ git stash 규칙 §5-6)
```

### 6단계 — Remote Control 1회성 수락 (대화형)
```bash
cd ~/gamer4info
claude                      # 워크스페이스 신뢰 다이얼로그 수락 → 종료
claude remote-control --name gamer4-ops --spawn worktree --capacity 3 \
  --permission-mode default --no-chrome --verbose
#   → "Enable Remote Control? (y/n)" 에 y
#   → 세션 URL 표시. 스페이스바로 QR → 폰 Claude 앱으로 스캔
# 폰에서 대화가 오가는지 확인한 뒤 Ctrl+C
```
폰에서 `/config` → **Push when Claude decides** / **Push when actions required** 활성화.

### 7단계 — systemd 상시화
```bash
# /home/ubuntu/bin/claude-rc-wrap.sh 와 /etc/systemd/system/claude-rc@.service 설치 (§2-4)
sudo systemctl daemon-reload
sudo systemctl enable --now claude-rc@ops
systemctl status claude-rc@ops
# 기존 텔레그램 메인 봇은 중지, boot-manager 봇은 유지
sudo systemctl disable --now claude-telegram
```

### 8단계 — idle-watch 수정 (§2-8, 필수)
tmux 화면 해시 대신 **대화 트랜스크립트 mtime + `~/.claude/jobs` 상태**를 보도록 고친다. 고치기 전까지는 `IDLE_LIMIT`를 크게(예: 288 = 24시간) 두거나 cron을 잠시 내려 작업 중 종료를 막는다.

### 9단계 — 스모크 테스트 (프로덕션 무영향 항목만)
```bash
set -a; . ~/.config/gamer4/secrets.env; set +a
npx vercel ls gamer4-info --scope byseops-projects --prod --token "$VERCEL_TOKEN"
npx vercel metrics --all vercel.function_invocation.count --group-by route --since 1d --scope byseops-projects
cd ~/gamer4info/infra && sam logs --stack-name gamer4-sync --start-time '10min ago' | tail -20
python3 ~/bin/sb_sql.py <읽기전용 select .sql>      # SUPABASE_ACCESS_TOKEN 판 (§5-3)
cd ~/gamer4info && yarn verify:isr
```
폰에서: 세션 목록에 `gamer4-ops`가 초록 점으로 보이는지, "지금 운영 상태 요약해줘" 응답, **`ask` 규칙에 걸리는 명령(`sam deploy --help` 등)을 시켰을 때 퍼미션 프롬프트가 폰에 뜨는지** 확인.
**`yarn build`는 마지막에 승인 후 1회만** 돌려 ~917페이지 빌드가 OOM 없이 끝나는지 확인한다.

---

## 7. 리스크 · 비용

### 비용 (ap-northeast-2, 온디맨드, ±10% — 공식 가격표 미확인)

| 구성 | 24시간 상시 | 하루 3시간(유휴 자동종료 유지) |
|---|---|---|
| t3.medium (현재, 세션 2개) | ~$38/월 | ~$5/월 |
| t3.large (세션 4~5개 권장) | ~$76/월 | ~$9/월 |
| t3.xlarge (세션 8+) | ~$152/월 | ~$19/월 |
| EBS 24 GB gp3 (**정지 중에도 과금**) | ~$2/월 | ~$2/월 |
| EBS 40 GB gp3 | ~$3.6/월 | ~$3.6/월 |
| Lambda·API GW·EventBridge·SSM | 프리티어 | 프리티어 |

프로젝트 목표는 "Vercel Pro $20 외 $0"이다. **24시간 상시 구동은 그 원칙을 정면으로 깬다.** 유휴 자동종료를 반드시 유지하고, 상시 구동은 검토 대상에서 제외하기를 권고한다. (Vercel Spend Management 예산이 이미 $30 — 여기에 AWS $38~76이 붙으면 체감이 크다.)
**t3 버스터블 크레딧 주의**: 세션 여러 개가 동시에 툴을 돌리면 baseline을 넘겨 스로틀되거나 unlimited 모드 추가 과금이 발생한다. Remote Control은 폴링 기반이라 유휴 시 CPU는 낮지만, 작업 중에는 세션당 10% 안팎을 쓴다(실측 pid 45273 = 9.6%).

### 인증·연결 함정 재발 방지
- **가장 큰 함정은 §2-2** — `CLAUDE_CODE_OAUTH_TOKEN`과 Remote Control은 **양립 불가**다. 과거 401 무한재발 사건의 해결책이 이번엔 원인이 된다. 전환 시 `.env`에서 토큰을 지우는 것이 첫 단계여야 한다.
- `claude-supervise.sh`의 credentials 가드는 `.env`에 `CLAUDE_CODE_OAUTH_TOKEN`이 있을 때만 동작하도록 이미 조건이 걸려 있다 → 토큰을 지우면 자동 무력화된다. **그 조건문을 지우지 말 것.**
- 진단: `claude doctor`가 Remote Control 적격성을 항목별로 출력한다. 로그인 문제는 `claude auth logout && claude auth login`.
- 연결 실패 메시지는 원인별로 다르다 — "다른 기기가 세션을 가져감" / "다른 앱에서 종료·아카이브됨" / "서버가 세션을 더 이상 보고하지 않음". **무턱대고 `/remote-control`을 재실행하면 다른 기기에서 세션을 빼앗는다.**

### 보안
1. **`SUPABASE_SERVICE_KEY`가 EC2 디스크에 놓인다.** 유출 시 DB 전체 노출. SG `launch-wizard-1`에 **SSH 22가 0.0.0.0/0으로 열려 있다**(메모리) — 실제로는 SSM만 쓰고 Remote Control도 인바운드가 필요 없으므로 **22번 닫기를 선행 조치로 권고**(무료, 되돌리기 쉬움).
2. **AWS 키 복사 금지.** 맥 자격증명은 `user/byseop-admin`(admin 추정). 인스턴스 탈취 = 계정 탈취가 된다. **`ec2-ssm-role`에 최소권한 인라인 정책**:
   - `cloudformation:*Stack*`(리소스 `gamer4-sync/*`), `lambda:UpdateFunctionCode|GetFunction|InvokeFunction`(`gamer4-sync-*`), `logs:FilterLogEvents|GetLogEvents`(`/aws/lambda/gamer4-*`), `s3:PutObject|GetObject`(SAM 아티팩트 버킷), `iam:PassRole`(람다 실행 역할 한정), `scheduler:GetSchedule|UpdateSchedule`.
   - 기존 `AmazonSSMManagedInstanceCore` + 자기 인스턴스 `ec2:StopInstances`는 유지. **`iam:*`·타 스택·타 리전은 부여하지 않는다.**
3. **트랜스크립트가 Anthropic 서버에 저장된다**(§2-10). 세션에서 `.env` 값이나 service_role 키를 출력하지 않도록 `permissions.deny`에 `Read(./.env)`를 넣는다.
4. **"항상 허용" 버튼.** 폰에서 프로덕션 조작 프롬프트에 "항상 허용"을 누르면 게이트가 영구 소멸한다. 운영 규칙으로 금지하고 `permissions.allow`를 주기 감사한다.
5. **감사 추적.** 폰 조작은 "무엇을 승인했는지"가 채팅에 흩어진다. **OPERATIONS §7 운영 로그 기록 규칙이 더 중요해진다** — EC2 CLAUDE.md에 "프로덕션 영향 작업은 실행 직후 §7 기록"을 명시.
6. **Remote Control 세션은 계정 소유자만 본다**(자동 연결도 본인 계정). 다만 claude.ai 계정이 털리면 EC2 셸에 접근하는 것과 같다 → 계정 2FA 필수.

### 기능적 리스크
- **`yarn dev` 로컬 브라우저 확인 불가.** 프리뷰 URL curl 검증은 유지되지만 시각 확인은 못 한다.
- **`next build` 메모리.** 917페이지 사전 생성을 4 GiB에서 돌린 적이 없다. 검증 전엔 "될 것"이라 가정하지 말 것.
- **`/plugin`·`/resume`은 폰에서 못 쓴다.** 플러그인 설치·대화 전환은 SSM 셸에서 해야 한다.

---

## 8. 권고안

### 권고 A (채택 권고) — **Remote Control 기반, 운영 모니터링 중심**

구성:
- **인스턴스**: t3.medium 유지(세션 2개) 또는 t3.large 상향(세션 4개). **볼륨 40 GB 확장 권장.**
- **세션**: `claude remote-control --name gamer4-ops --spawn worktree --capacity 3 --permission-mode default --no-chrome`, systemd `claude-rc@ops` 로 상시. 기능 작업은 앱에서 세션을 추가하면 워크트리가 자동 생성.
- **승인**: Manual 모드 + `permissions.ask` 규칙(= OPERATIONS §1-3 목록과 1:1). 프롬프트가 폰으로 푸시된다. 자작 훅·Lambda `/approve` **불필요**.
- **전원**: boot-manager Lambda(`/start` `/stop` `/status`) 유지 + 수정된 `idle-watch.sh` 60분 유휴 종료.
- **맥에 남기는 것**: 브라우저가 필요한 모든 것(GSC, Vercel 대시보드, 디자인 QA), `yarn dev` 시각 확인, 대형 빌드. 그리고 **맥 세션에서도 `/remote-control`을 켜두면 같은 대화를 폰에서 이어받을 수 있다** — 이게 원래 이 기능의 용도다.

**도입 순서**: ① SG 22 닫기 → ② 인스턴스 역할 최소권한 → ③ `claude install latest` + `claude auth login`(토큰 제거 선행) → ④ 리포·설정·플러그인 이식 → ⑤ Remote Control 1회성 수락 + 폰 연결 확인 → ⑥ `idle-watch.sh` 수정 → ⑦ systemd 상시화 → ⑧ 1주 병행 운용(맥이 정답, EC2가 검증) → ⑨ 모니터링 세션 이관.

### 권고 B (비권고) — 전부 이전, 세션 6개+
디스크·RAM·CPU 크레딧이 전부 상향돼야 하고(t3.xlarge 24시간이면 월 $150+), 그러고도 폰에서 대형 diff를 리뷰하는 문제는 남는다. 그리고 §2-6대로 **서브에이전트가 이미 병렬이라 세션을 늘려 얻는 이득이 작다.**

### 권고 C (병행) — 클라우드 세션 + 스케줄 + 푸시
- `/schedule`(cron 클라우드 에이전트)로 "매일 회차 검증 → 이상 시 보고"를 돌리면 **EC2를 켜 둘 필요 자체가 없다.** 인스턴스 비용 $0, 인증 함정 무관, service_role 키를 EC2에 둘 이유도 없다.
- 클라우드 세션은 Remote Control과 같은 claude.ai/code 화면을 쓰므로 **폰 UX가 동일**하다. 차이는 실행 위치뿐.
- 알림은 이미 있다: CloudWatch Alarm → SNS → `byseop@gmail.com`. Remote Control 푸시까지 더하면 이중화.
- 한계: 클라우드 세션에서 `sam deploy`·Vercel 프로덕션 조작이 가능한지 확인하지 못했다(§9). **정기 점검·보고는 C, 실행이 필요한 순간만 A의 EC2를 켠다.**

### 최종 한 줄
**A + C를 권고한다.** 정기 점검·보고는 C로 무비용 자동화하고, 사람이 개입해야 하는 순간에만 EC2를 켜서 Remote Control 세션(A)을 쓴다. 세션은 **상시 `ops` 1개 + 온디맨드 기능 세션 1~2개**로 제한하고, 승인은 `permissions.ask` + 폰 푸시로 받는다. 코드 작업의 무게중심은 맥에 남긴다.

---

## 9. 확인하지 못한 것 (정직한 미지)

1. **TTY 없이 systemd에서 `claude remote-control`이 정상 동작하는지.** 문서는 tmux/screen을 권장할 뿐 systemd 직접 실행을 언급하지 않는다. tmux 래퍼(§2-4)를 전제로 설계했다.
2. **EC2의 실제 상태** — SSM 접속을 하지 않아 `describe-*` 메타데이터만 봤다. 디스크 여유, 설치 패키지, `claude` 실제 버전(2026-05-21 메모리 기준 2.1.146)은 미확인.
3. **t3.medium 4 GiB에서 `next build`(917페이지) 성공 여부.**
4. **EC2 인스턴스 시간당 정확한 요금** — 공식 가격표를 확인하지 못했다. 표의 값은 통상 알려진 수치의 근사이고 ±10% 오차를 가정해야 한다.
5. **Claude Code 클라우드 세션에서 임의 CLI(`sam`, `vercel`)를 쓸 수 있는 범위.**
6. **`--spawn worktree`가 만드는 워크트리의 위치·명명 규칙**과 기존 `.claude/worktrees/` 구조와의 충돌 여부.
7. 맥 메인 체크아웃 `.env`에 `REVALIDATE_SECRET`가 없고 **워크트리 `.env`에만 있다** — 어느 쪽이 정본인지 운영자 확인 필요(이전과 별개로 지금도 드리프트).
8. 텔레그램 채널 플러그인의 파일·이미지 전송 가능 여부(폴백 경로를 평가할 때만 필요).

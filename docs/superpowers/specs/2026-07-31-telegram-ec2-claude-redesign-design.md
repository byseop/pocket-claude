# 텔레그램 ↔ EC2 Claude Code 원격 제어 재설계

**작성일**: 2026-07-31
**상태**: 승인됨
**대상**: `ec2-boot-manager` Lambda v4 + EC2 `claude-code-machine` 기동 구조

## 배경

2026-07-30, 메인 클로드 봇이 텔레그램 메시지에 응답하지 않는 장애가 발생했다. 조사 결과 두 층의 문제가 드러났다.

**직접 원인**: `~/.claude/.env`의 `CLAUDE_CODE_OAUTH_TOKEN`이 폐기(revoked)됐다. 메시지는 텔레그램 → MCP 서버 → claude 세션까지 정상 도달했으나 응답 생성 시점에 `401 OAuth access token has been revoked`로 막혔다. 프로세스는 살아 있어서 외부에서는 원인을 구분할 수 없었다. 2026-07-31 04:18에 토큰 재발급으로 복구했다.

**구조적 원인**: 조사 중 기동 경로에서 별개의 결함 세 개가 확인됐다.

1. **`pgrep` 가드 오탐** — `lambda_function.py:228`의 `pgrep -f "claude" >/dev/null && exit 0`. SSM RunShellScript는 `commands` 배열을 하나의 스크립트로 합쳐 실행하므로 `exit 0`이면 다음 줄의 tmux 기동이 통째로 건너뛰어진다. 그런데 이 패턴은 `/home/ubuntu/.local/share/claude/versions/...` 경로를 가진 CLI 자동 업데이트 프로세스에도 매칭된다. 실제로 7/30 09:31:28에 SSM 기동 명령이 전송됐고 09:31:38에 claude 2.1.220 다운로드가 완료됐다 — 10초 이내로 겹친다.
2. **API Gateway 타임아웃 초과** — 통합 타임아웃이 30,000ms인데 `/start_ec2`는 `instance_running` waiter와 `sleep 20` 때문에 38,757ms가 걸렸다. 텔레그램은 503을 받고 같은 업데이트를 재전송하는데, 재실행된 Lambda는 `lambda_function.py:213-215`에서 이미 `running`이므로 "이미 실행 중"만 답하고 기동 없이 종료한다.
3. **자동 복구 부재** — 부팅 시 자동 실행 설정(systemd/cron/rc.local)이 없어 기동이 전적으로 Lambda의 SSM 호출에 의존했다. 그 경로가 실패하면 아무도 재시도하지 않았다.

세 결함은 모두 **"Lambda가 EC2 안에서 클로드를 기동시킨다"**는 하나의 설계 결정에서 파생됐다. 원격에서 남의 프로세스를 띄우려니 기다려야 했고(타임아웃), 중복 실행을 막으려니 가드가 필요했고(오탐), 실패해도 복구 주체가 없었다.

## 설계 원칙

**기동 책임을 EC2 자신에게 넘긴다.** 이 한 가지 변경으로 세 결함이 코드 패치가 아니라 구조적으로 소멸한다. Lambda의 역할은 전원 스위치와 조회창 둘로 축소된다.

부수적으로, 텔레그램 채널 플러그인에 세션 생성 기능이 없어서 과하게 붙었던 명령들(`/new`, `/list`, `/sess`, `/cmd`, `/respawn` 등은 v3에서 이미 제거됨, `/restart_main`과 `/diag`는 이번에 제거)을 정리해 4개로 줄인다.

## 아키텍처

```
[모바일 텔레그램]
 ├─ @<boot-manager-bot> ──웹훅──▶ API GW ──▶ Lambda(ec2-boot-manager v4)
 │     /start /stop /status /view          ├─ ec2:Start/StopInstances  (즉시 반환)
 │                                          └─ ssm:SendCommand         (조회 전용)
 └─ @<main-claude-bot> ──롱폴링──▶ EC2 안 claude 세션 (대화)

[EC2 claude-code-machine]
 systemd: claude-telegram.service (Restart=always)
    └─ claude-supervise.sh
         └─ tmux 'claude'
              └─ claude --dangerously-skip-permissions --channels plugin:telegram@claude-plugins-official
 cron(5분): idle-watch.sh ──▶ 자기 자신 stop
```

봇 2개 구성은 유지한다. 부트매니저 봇은 EC2가 꺼져 있어도 응답해야 하므로 Lambda 쪽에 있어야 하고, 한 봇 토큰으로 웹훅(Lambda)과 롱폴링(EC2 플러그인)을 동시에 쓸 수 없기 때문에 통합이 불가능하다.

## 컴포넌트

### 1. `claude-supervise.sh` (신규, EC2)

**목적**: systemd가 추적 가능한 프로세스를 제공하면서 tmux 세션을 유지한다.

`ExecStart=tmux new-session -d`를 직접 쓰면 tmux 서버가 스스로 데몬화(double fork + setsid)해 systemd가 프로세스를 추적하지 못한다. 서비스가 죽은 것으로 오판돼 `Restart=always`가 무한 재시작 루프를 돈다. 얇은 래퍼로 이를 회피한다.

```bash
#!/bin/bash
# Clear stale idle counter so a boot right after an idle shutdown does not
# immediately trigger another shutdown
rm -f /home/ubuntu/.claude-idle-state

tmux has-session -t claude 2>/dev/null || \
  tmux new-session -d -s claude '/home/ubuntu/start-claude-telegram.sh'
# Block while the session lives; exit non-zero when it dies so systemd restarts us
while tmux has-session -t claude 2>/dev/null; do sleep 10; done
exit 1
```

**의존**: tmux, `/home/ubuntu/start-claude-telegram.sh` (기존 파일 그대로 유지)

### 2. `claude-telegram.service` (신규, EC2)

```ini
[Unit]
Description=Claude Code Telegram channel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Environment=HOME=/home/ubuntu
ExecStart=/home/ubuntu/claude-supervise.sh
ExecStop=/usr/bin/tmux kill-session -t claude
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`systemctl enable`로 부팅 시 자동 시작한다. 이 시점부터 Lambda는 기동에 관여하지 않는다.

### 3. `idle-watch.sh` (신규, EC2, cron 5분)

**목적**: 켜두고 잊었을 때의 과금을 막는다. t3.medium 24시간 방치는 월 4만원대다.

tmux 화면 해시를 이전 값과 비교해 12회 연속(=60분) 동일하면 텔레그램으로 알린 뒤 자기 자신을 stop 한다.

- 실행 주체: `ubuntu` 유저의 crontab. tmux 세션과 상태 파일 소유자가 일치해야 한다
- 상태 파일: `/home/ubuntu/.claude-idle-state` (`<해시> <카운터>` 한 줄). **부팅 시 반드시 초기화한다** — 종료 직전의 카운터 12가 남아 있으면 다음 기동 직후 즉시 다시 꺼진다. `claude-supervise.sh` 시작부에서 이 파일을 삭제해 처리한다
- 인스턴스 ID: IMDSv2로 자기 조회
- 알림: EC2에 이미 있는 메인 봇 토큰(`~/.claude/channels/telegram/.env`)을 재사용해 부품을 늘리지 않는다

**검증된 의존성** (2026-07-31 실측):

| 의존 | 상태 |
|---|---|
| AWS CLI | `/usr/local/bin/aws` 2.34.26 |
| IMDSv2 | 정상, `${INSTANCE_ID}` 반환 |
| cron 데몬 | active |
| `ubuntu` 유저 tmux 캡처 | 정상 |

**전제**: "화면이 멈춰 있으면 유휴"로 판단한다. 작업 중에는 `✦ Working… (12s)` 타이머가 돌아 화면이 바뀌므로 오판되지 않고, 대기 상태에서 화면이 정지하는 것은 실측으로 확인했다. 향후 TUI가 대기 중에도 깜빡이도록 바뀌면 영원히 종료되지 않을 수 있어 검증 단계에서 확인한다.

### 4. IAM 인라인 정책 (`ec2-ssm-role`)

현재 이 역할에는 `AmazonSSMManagedInstanceCore`만 붙어 있어 자기 자신을 stop 할 권한이 없다. 자기 인스턴스 하나로만 제한한 인라인 정책을 추가한다.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "ec2:StopInstances",
    "Resource": "arn:aws:ec2:ap-northeast-2:${AWS_ACCOUNT_ID}:instance/${INSTANCE_ID}"
  }]
}
```

Lambda 실행 역할(`<lambda-execution-role>`)은 이미 필요한 권한을 모두 갖고 있어 변경하지 않는다.

### 5. Lambda v4 (`ec2-boot-manager`)

**핵심 제약**: 어떤 경로에서도 블로킹하지 않는다. `waiter`와 `sleep`을 전부 제거한다. 이로써 API Gateway 30초 타임아웃이 구조적으로 발생 불가능해진다 (최장 경로는 `/status`의 SSM 조회 약 3초).

| 명령 | 동작 | 응답 시간 |
|---|---|---|
| `/start` | `start_instances` 호출 후 즉시 응답. 기동은 systemd 담당 | < 1초 |
| `/stop` | `stop_instances` 호출 후 즉시 응답 | < 1초 |
| `/status` | EC2 상태 + 서비스 생존 + 인증 상태 | 2~3초 |
| `/view` | tmux 화면 캡처 (ANSI 제거 후 전송) | 2~3초 |

**제거 대상**: `/restart_main`(systemd가 대신함), `/diag`(`/status`에 흡수), `pgrep` 가드, `waiter`, `sleep 20`.

**유지**: `ALLOWED_CHAT_ID` 화이트리스트(`${ALLOWED_CHAT_ID}`).

#### `/status` 출력

```
✅ EC2 running (2h 13m)
✅ claude-telegram.service active
✅ 인증 OK
```

인증 실패 시 — 복구 절차를 명령이 아니라 응답 본문에 그대로 담는다. 명령을 4개로 유지하고, 장애 상황에서 추가 왕복 없이 바로 조치할 수 있게 한다:

```
❌ 인증 만료 — 마지막 에러 14:02
   "OAuth access token has been revoked"

   복구:
   1. SSM으로 접속 후 sudo -u ubuntu -i
   2. claude setup-token
   3. umask 077
      echo 'export CLAUDE_CODE_OAUTH_TOKEN=<토큰>' > ~/.claude/.env
   4. sudo systemctl restart claude-telegram
```

`claude setup-token`은 브라우저 인증이 필요한 인터랙티브 명령이라 봇이 대신 수행할 수 없다. 자동화 가능한 부분은 4번(재시작)뿐이며, 이는 systemd가 담당한다.

**인증 감지 방식**: tmux 스크롤백 200줄에서 `401|OAuth|Please run /login` 패턴을 찾는다. 실제 API를 호출해 검사하는 방법도 있으나 토큰을 소모하고 느리다. 이번 장애 때 실제로 화면에 흔적이 남았으므로 화면을 읽는 쪽이 가볍고 충분하다.

#### `/view` 출력

`tmux capture-pane -p`의 결과에서 ANSI escape sequence를 `sed`로 제거한 뒤 전송한다. `claude logs`가 ANSI로 도배돼 모바일에서 읽을 수 없었던 기존 문제를 피한다.

#### 부팅 중 상태 처리

`/start` 직후 `/status`를 치면 SSM 에이전트가 아직 준비되지 않았을 수 있다. `ssm:DescribeInstanceInformation`의 `PingStatus`로 판별해 "부팅 중 — SSM 대기"를 안내한다.

## 데이터 흐름

**시스템 명령** (`/start`, `/stop`, `/status`, `/view`)
```
텔레그램 → 웹훅 → API GW → Lambda → (ec2 API | ssm SendCommand) → Lambda가 sendMessage로 응답
```

**대화**
```
텔레그램 → (롱폴링) MCP 서버(bun) → claude 세션 → reply 도구 → 텔레그램
```

두 흐름은 서로 다른 봇 토큰을 쓰며 교차하지 않는다.

## 에러 처리

| 상황 | 처리 |
|---|---|
| `/start` 직후 `/status` | `PingStatus`로 판별해 "부팅 중 — SSM 대기" 안내 |
| API GW 30초 타임아웃 | Lambda가 블로킹하지 않아 구조적으로 발생 불가 |
| SSM 명령 실패 | 에러 텍스트를 그대로 텔레그램에 전달 (조용한 실패 금지) |
| tmux 세션 사망 | `claude-supervise.sh`가 감지해 종료 → systemd가 10초 뒤 재시작 |
| 인증 만료 | 프로세스는 살아 있어 재시작 루프가 생기지 않음. `/status`가 명시적으로 표시 |
| EC2가 stopped인데 `/status`/`/view` | SSM 조회를 건너뛰고 "EC2 꺼져 있음 — /start" 안내 |

## 검증 계획

1. `tmux kill-session -t claude` → 10초 내 자동 재생성되는가
2. EC2 stop → start → 텔레그램 대화가 아무 명령 없이 바로 되는가 (systemd 자기부팅)
3. `/start` 응답이 1초 미만인가 (타임아웃 결함 해소)
4. 유휴 임계값을 임시로 2회(10분)로 낮춰 자동 종료 동작 확인 후 12회로 복원
5. 인증을 일부러 깨뜨려 `/status`가 잡아내는가
6. `/view` 출력에 ANSI escape가 남아 있지 않은가
7. 유휴 종료 직후 `/start`로 다시 켰을 때 즉시 재종료되지 않는가 (상태 파일 초기화 확인)

## 작업 순서

EC2 쪽을 먼저 올려 자기부팅을 확인한 뒤 마지막에 Lambda를 교체한다. 중간에 실패해도 기존 Lambda v3가 그대로 살아 있어 롤백이 필요 없다.

1. `claude-supervise.sh` + `claude-telegram.service` 설치, `systemctl enable --now`
2. IAM 인라인 정책 추가
3. `idle-watch.sh` + cron 설치
4. 검증 1~2 (자기부팅·자동복구)
5. Lambda v4 배포
6. 검증 3~6

## 참고

- 기존 소스: `~/Documents/workspace/telegram-claude-ec2/src/lambda_function.py` (v3)
- 백업: `backup/lambda_function.{v1,v2}.py` — v4 배포 전 v3도 백업한다
- EC2: `${INSTANCE_ID}`, `ap-northeast-2`, 계정 `${AWS_ACCOUNT_ID}`
- 텔레그램 채널 플러그인은 v0.0.6이며 노출 도구는 `reply`/`react`/`download_attachment`/`edit_message` 4개뿐이다. 세션 생성 기능은 없으며, 이 설계는 그 부재를 전제로 한다.

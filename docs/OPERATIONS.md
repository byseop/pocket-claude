# 운영 및 트러블슈팅

pocket-claude를 운영하면서 실제로 겪은 고장과 그 진단법을 기록한다. 설계 의도는
[재설계 설계 문서](superpowers/specs/2026-07-31-telegram-ec2-claude-redesign-design.md)를,
구현 절차는 [구현 계획](superpowers/plans/2026-07-31-pocket-claude-redesign.md)을 참조한다.

- 리전: `ap-northeast-2`
- 인스턴스: `claude-code-machine`
- Lambda: `ec2-boot-manager` (v4)

계정 ID와 인스턴스 ID는 저장소에 두지 않는다. 로컬 `.env`와 Lambda 환경변수에만 있다.
아래 명령에서 인스턴스를 지정해야 할 때는 `.env`를 소싱해 `$INSTANCE_ID`를 쓴다.

## 일상 운영

텔레그램 부트매니저 봇에서 네 개의 명령을 쓴다.

| 명령 | 하는 일 | 응답 |
|---|---|---|
| `/start` | 인스턴스 시작. **클로드 기동에는 관여하지 않는다** — systemd가 알아서 띄운다 | 1초 미만 |
| `/stop` | 인스턴스 정지 | 1초 미만 |
| `/status` | 인스턴스 상태 + 서비스 생존 + 인증 상태 | 2~3초 |
| `/view` | tmux 화면 (ANSI 제거) | 2~3초 |

대화는 메인 클로드 봇에서 한다. 두 봇은 토큰이 다르고 경로가 겹치지 않는다.

인스턴스는 60분 유휴 시 스스로 정지하며 정지 직전 텔레그램으로 알린다.

## 인증 문제 진단

이 절이 이 문서의 핵심이다. 401은 **원인이 두 가지**이고, 증상이 비슷한데 해법이
전혀 다르다. 아래 순서대로 좁힌다.

### 1단계 — 토큰 자체가 살아 있는가

```bash
sudo -u ubuntu bash -c '
  export HOME=/home/ubuntu PATH=/home/ubuntu/.local/bin:$PATH
  . ~/.claude/.env
  claude -p "Reply with exactly: PONG"'
```

`PONG`이 나오면 **토큰은 멀쩡하다.** 여기서 토큰을 재발급하는 것은 시간 낭비이며,
실제로 그렇게 여러 번 재발급했다가 원인을 못 찾은 사례가 있다. 2단계로 간다.

401이 나오면 토큰이 실제로 폐기된 것이다. [토큰 재발급](#토큰-재발급)으로 간다.

### 2단계 — 메인 세션은 되는가

```bash
sudo -u ubuntu tmux send-keys -t claude "Reply with exactly: PONG" Enter
sleep 25
sudo -u ubuntu tmux capture-pane -t claude -p | tail -12
```

**`claude -p`는 되는데 tmux 세션은 401** → 함정 A (만료 credentials).
**메인은 되는데 백그라운드만 401** → 함정 B (낡은 데몬).

### 함정 A — 만료된 `.credentials.json`이 토큰을 가린다

대화형(`--channels`) 경로는 `~/.claude/.credentials.json`을 **환경변수
`CLAUDE_CODE_OAUTH_TOKEN`보다 먼저** 집어든다. 그 파일이 만료됐으면 갱신을 시도하고,
리프레시 토큰도 죽어 있으면 `401 OAuth access token has been revoked`를 내면서
**환경변수로 폴백하지 않는다.**

그래서 토큰을 아무리 새로 발급해 `.env`에 넣어도 소용이 없다. 새 토큰은 애초에
사용되지 않는다. `claude -p`는 다른 경로라 정상 동작하기 때문에 "토큰은 멀쩡한데
봇만 죽는" 혼란스러운 상태가 된다.

확인:

```bash
python3 -c "
import json, datetime
o = json.load(open('/home/ubuntu/.claude/.credentials.json'))['claudeAiOauth']
t = datetime.datetime.fromtimestamp(o['expiresAt'] / 1000)
print(t, '->', '만료' if t < datetime.datetime.now() else '유효')"
```

수동 복구는 파일을 치우고 재시작하는 것이다.

```bash
mv ~/.claude/.credentials.json ~/.claude/.credentials.json.expired
sudo systemctl restart claude-telegram
```

**자동화되어 있다.** `ec2/claude-supervise.sh`가 기동 시 만료 여부를 확인해 만료된
경우에만 치운다. 유효한 파일은 건드리지 않는다. `logger`로 syslog에 남는다.

### 함정 B — 자격증명보다 오래된 supervisor 데몬

`claude --bg`는 새 데몬을 띄우지 않고 **이미 떠 있는 supervisor에 작업을 넘긴다.**
인증이 깨진 시점에 뜬 데몬은 그 상태를 계속 물고 있어, 토큰을 갱신해도 **그 이후
생성한 bg 세션까지 전부** 401로 막힌다.

표식은 작업 상태 파일의 `providerEnv`가 비어 있는 것이다.

```bash
python3 -c "
import json
s = json.load(open('/home/ubuntu/.claude/jobs/<id>/state.json'))
print(s['state'], s['needs'], s['providerEnv'])"
# blocked / login required — run /login / {}
```

셸에서 직접 `claude --bg`를 띄워도 똑같이 실패하기 때문에 환경변수 전달 문제로
오인하기 쉽다. 실제로 그 가설을 먼저 세웠다가 실측으로 기각했다. 셸에서 띄우든
클로드 안에서 띄우든 **같은 데몬**을 거치므로 결과가 같다.

수동 복구:

```bash
pkill -u ubuntu -f 'claude daemon run'
rm -rf /tmp/cc-daemon-$(id -u ubuntu)
```

데몬을 다시 띄우면 **막혀 있던 기존 세션까지 이어서 완료된다.** 재실행할 필요가 없다.

**자동화되어 있다.** `claude-supervise.sh`가 기동 시 데몬 시작 시각과 `.env` mtime을
비교해, 자격증명이 더 최신일 때만 데몬을 정리한다. 데몬이 더 최신이면 건드리지
않으므로 일반 재시작에서는 진행 중인 bg 작업이 살아남는다.

### 토큰 재발급

1단계에서 실제로 토큰이 죽었다고 확인된 경우에만 한다.

```bash
claude setup-token          # 브라우저 인증 필요. 봇이 대신 못 한다
umask 077
echo 'export CLAUDE_CODE_OAUTH_TOKEN=<발급받은_토큰>' > ~/.claude/.env
sudo systemctl restart claude-telegram
```

`claude setup-token`은 토큰을 **발급만** 하고 `.credentials.json`에 저장하지 않는다.
직접 `.env`에 넣어야 한다.

재시작하면 위 두 가드가 함께 돈다 — 만료된 credentials를 치우고, `.env`보다 오래된
데몬을 정리한다. 그래서 재시작 한 번으로 두 함정이 모두 해소된다.

## 서비스 관리

```bash
systemctl status claude-telegram
systemctl restart claude-telegram
journalctl -u claude-telegram -n 50
grep claude-supervise /var/log/syslog | tail    # 가드 동작 기록
grep idle-watch /var/log/syslog | tail          # 유휴 감시 기록
```

`claude-supervise.sh`가 systemd가 추적할 프로세스 역할을 한다. tmux 세션이 죽으면
래퍼가 비정상 종료하고 `Restart=always`가 약 10초 뒤 되살린다. 실측상 세션 강제
종료 후 20초 이내에 복구된다.

`ExecStart`에서 `tmux new-session -d`를 직접 부르면 안 된다. tmux 서버가 스스로
데몬화해 systemd가 PID를 놓치고, 서비스가 죽은 것으로 오판돼 재시작 루프가 돈다.
래퍼는 이걸 피하려고 있다.

## 유휴 자동 종료

5분마다 cron이 tmux 화면 해시를 비교해 12회 연속(60분) 동일하면 정지한다.

판정 전에 `~/.claude/jobs/`를 확인한다. `claude --bg` 에이전트가 작업하는 동안
메인 화면은 정지해 있어서, 화면 해시만 보면 작업 중인 인스턴스를 꺼버린다.
작업 중으로 판정하는 조건은 두 축이다.

- 상태가 종료 상태가 아닐 것. `blocked`는 **종료 상태로 분류한다** — 사람 입력을
  기다리는 상태라, 그러지 않으면 방치된 세션이 인스턴스를 영구히 붙잡는다
- 30분 이내에 갱신되었을 것. 비종료 상태로 멈춰버린 좀비 세션을 걸러낸다

임계값 조정은 환경변수로 한다. 검증할 때 60분을 기다릴 필요가 없다.

```bash
IDLE_LIMIT=2 /home/ubuntu/idle-watch.sh      # 2회(10분)로 단축
BG_STALE_SECONDS=1800                        # 좀비 판정 기준
```

상태 파일 `/home/ubuntu/.claude-idle-state`는 `<해시> <카운터>` 한 줄이다. 부팅 시
`claude-supervise.sh`가 지운다. 이게 없으면 정지 직전의 카운터를 물려받아 다음 기동
직후 다시 꺼진다.

**알려진 한계**: "화면이 멈춰 있으면 유휴"라는 전제에 기댄다. 향후 TUI가 대기
중에도 화면을 갱신하도록 바뀌면 영원히 종료되지 않는다. 증상은 "인스턴스가 안
꺼짐"이고, 상태 파일의 카운터가 계속 0이면 이 경우다.

## 배포

### EC2 스크립트

`ec2/` 아래 파일들을 인스턴스로 보내고 `install.sh`를 실행한다. 멱등하므로 여러 번
돌려도 된다.

SSM `--parameters`에 한글이나 따옴표가 섞이면 CLI 파싱이 깨지므로 JSON 파일로
전달한다. 그리고 **SSM RunShellScript는 bash가 아니라 `/bin/sh`(dash)로 실행된다** —
프로세스 치환 `<(...)` 같은 bash 전용 문법을 쓰면 `Syntax error: "(" unexpected`로
죽는다.

또 하나, SSM은 마지막 명령의 종료 코드로 성공/실패를 판정한다. `grep -c`로 끝나는
스크립트는 매칭이 0건일 때 grep이 1을 반환해서 **원하는 결과가 나왔는데도 `Failed`로
표시된다.** `|| true`를 붙인다.

### Lambda

```bash
cd src && zip ../lambda.zip lambda_function.py
aws lambda update-function-code --function-name ec2-boot-manager --zip-file fileb://../lambda.zip
aws lambda wait function-updated --function-name ec2-boot-manager
```

환경변수는 `TELEGRAM_TOKEN`, `ALLOWED_CHAT_ID`, `INSTANCE_ID` 세 개다.
`update-function-configuration`은 전체를 덮어쓰므로 항상 셋을 함께 넘긴다.

배포 전에 테스트를 돌린다. 순수 헬퍼는 AWS 없이 검증된다.

```bash
python3 -m unittest discover -s tests
```

### 롤백

`backup/lambda_function.v3.py`는 인스턴스 ID가 하드코딩되어 있어 gitignore 대상이다.
로컬에만 있다.

```bash
mkdir -p /tmp/rb && cp backup/lambda_function.v3.py /tmp/rb/lambda_function.py
(cd /tmp/rb && zip -q rollback.zip lambda_function.py)
aws lambda update-function-code --function-name ec2-boot-manager --zip-file fileb:///tmp/rb/rollback.zip
```

EC2 쪽은 되돌릴 필요가 없다. systemd 유닛은 v3와 공존 가능하다.

## 사고 기록

### 2026-07-30 — 메인 봇 무응답

18:30 KST에 `/start_ec2`로 인스턴스를 켰으나 클로드가 뜨지 않았고, 다음 날까지
대화가 되지 않았다. 조사 결과 **하나의 설계 결정에서 파생된 결함 세 개**와 **별개의
인증 문제 하나**가 겹쳐 있었다.

| 시각(UTC) | 사건 |
|---|---|
| 09:30:50 | `/start_ec2` Lambda 시작 |
| 09:30:53 | 인스턴스 start |
| 09:31:20 | **API GW 30초 타임아웃 → 텔레그램에 503** |
| ~09:31:28 | Lambda가 SSM으로 tmux 기동 명령 전송 |
| 09:31:38 | claude 2.1.220 자동 업데이트 완료 |

기동 시도와 CLI 자동 업그레이드가 10초 이내로 겹쳤다.

**근본 원인 — Lambda가 EC2 안에서 클로드를 기동시키는 구조.** 원격에서 남의 프로세스를
띄우려니 기다려야 했고(타임아웃), 중복 실행을 막으려니 가드가 필요했고(오탐), 실패해도
복구 주체가 없었다.

1. `pgrep -f "claude"` 가드 오탐 — SSM은 `commands` 배열을 하나의 스크립트로 합쳐
   실행하므로 `exit 0`이면 다음 줄의 tmux 기동이 통째로 건너뛰어진다. 그런데 이 패턴은
   `.local/share/claude/versions/...` 경로를 가진 자동 업데이트 프로세스에도 매칭된다
2. API Gateway 통합 타임아웃 30,000ms인데 `/start_ec2`가 38,757ms 소요 — 텔레그램이
   503을 받고 같은 업데이트를 재전송하면, 재실행된 Lambda는 이미 `running`이라
   "이미 실행 중"만 답하고 기동 없이 종료한다
3. 부팅 자동 실행 설정 부재 — 기동이 전적으로 Lambda의 SSM 호출에 의존했다
4. (별개) 만료된 `.credentials.json`이 OAuth 토큰을 가림 — 위 함정 A

### 해소 결과

| 결함 | 조치 | 실측 |
|---|---|---|
| pgrep 오탐 | 가드 제거. systemd가 기동 담당 | — |
| API GW 타임아웃 | 블로킹 제거 | 38,757ms → 1,654ms |
| 자동 복구 부재 | `Restart=always` + 감시 래퍼 | 세션 강제 종료 후 20초 내 복구 |
| 만료 credentials | 기동 시 가드 | 401 0건 |
| 낡은 데몬 | 기동 시 가드 | bg 세션 `done` 확인 |
| Lambda 조용한 실패 | 최상위 예외를 텔레그램으로 | 잘못된 ID 주입으로 검증 |

## 알려진 제약

- **인터랙티브 인증 불가** — `claude setup-token`은 브라우저가 필요하다. 봇이 대신
  수행할 수 없으며, 자동화 가능한 부분은 재시작뿐이다
- **`/model`은 런타임에 못 바꾼다** — 대화형 명령이라 세션 안에서 사용자가 직접
  입력해야 한다. 영구 변경은 `~/.claude/settings.json`의 `model`을 고치고 재시작한다
- **로컬에 session-manager-plugin이 없으면** `aws ssm start-session`을 못 쓴다.
  모든 인스턴스 작업을 `aws ssm send-command`로 해야 한다
- **`ssm_run`의 25초 상한** — SSM이 느려지면 `/status`가 30초 한계에 근접할 수 있다.
  실측 3초 대비 여유가 있으나 Duration이 20,000ms를 넘기 시작하면 상한을 낮추고
  부분 결과를 반환하도록 바꾼다

# 운영 및 트러블슈팅

pocket-claude를 운영하면서 실제로 겪은 고장과 그 진단법을 기록한다. 설계 의도는
[재설계 설계 문서](superpowers/specs/2026-07-31-telegram-ec2-claude-redesign-design.md)를,
구현 절차는 [구현 계획](superpowers/plans/2026-07-31-pocket-claude-redesign.md)을 참조한다.
v5(Remote Control 모드)의 설계는 [Remote Control 설계 문서](superpowers/specs/2026-09-22-remote-control-mode-design.md),
구현 절차는 [v5 구현 계획](superpowers/plans/2026-09-22-remote-control-mode.md)을 참조한다.
v6(멀티 프로젝트)의 설계는 [범용화 설계 문서](superpowers/specs/2026-09-23-generalize-multi-project-design.md),
구현 절차는 [v6 구현 계획](superpowers/plans/2026-09-23-multi-project-phase1.md)을 참조한다.

- 리전: `ap-northeast-2`
- 인스턴스: `claude-code-machine`
- Lambda: `ec2-boot-manager` (v6)

계정 ID와 인스턴스 ID는 저장소에 두지 않는다. 로컬 `.env`와 Lambda 환경변수에만 있다.
아래 명령에서 인스턴스를 지정해야 할 때는 `.env`를 소싱해 `$INSTANCE_ID`를 쓴다.

## 일상 운영

대화는 **Claude 모바일 앱 Code 탭**에서 한다. 텔레그램 봇은 전원과 프로젝트 서버만
다룬다. 박스 쪽 로직은 모두 `~/bin/pocket` CLI에 있고, Lambda는 SSM으로 그 verb 하나를
부른 뒤 결과를 한글 문장·버튼으로 바꿀 뿐이다.

| 명령 | 하는 일 | 응답 |
|---|---|---|
| `/start` | 인스턴스 시작. 부팅되면 켜 둔 프로젝트들이 자동으로 뜬다(스티키) | 1초 미만 |
| `/stop` | 인스턴스 정지. 모든 프로젝트 오프라인 | 1초 미만 |
| `/status` | 인스턴스 상태(가동 시간 포함) + 프로젝트별 서버 + 인증 상태 | 2~3초 |
| `/projects` | 프로젝트별 상태·브랜치·마지막 대화 + [켜기/끄기/트리] 버튼 | 2~3초 |
| `/up <p>` | `pocket up <p>`: 신뢰·디스크 확인 후 enable + start | ~5초 |
| `/down <p>` | `pocket down <p>`: disable + stop | 2~3초 |
| `/trees <p>` | 그 프로젝트의 세션 워크트리 목록(미커밋·미푸시·잠김·사용중 표시) | 2~3초 |

`<p>`는 `[a-z0-9][a-z0-9-]{0,23}`이고 `~/work/<p>` 링크가 있어야 프로젝트로 인식된다.
`pocket prune <p>`(텔레그램 명령 아님, SSM 셸에서 직접)는 미커밋·미푸시·잠금·사용중
어느 것도 아닌 워크트리만 지운다. SSM 스크립트는 `sudo -u ubuntu`로 돌고 sudoers는
`systemctl start|stop|restart|enable|disable|reset-failed claude-rc@*`만 허용한다.

### 세션 종료의 의미

Remote Control 세션에 "종료"는 없다. 프로세스가 살아 있으면 온라인, 죽으면 몇 초 뒤
앱에서 오프라인이다.

- `/down <p>`: 유닛 정지 + disable. **닫기 전에 커밋·푸시하고 그 프로젝트의 운영 문서를
  갱신한다** (EC2 CLAUDE.md 규칙).
- `/stop`: 전 프로젝트 오프라인. `/up`으로 켜 둔 것만 다음 부팅에 자동으로 돌아온다
  (스티키). 약 4시간 이내 재기동이면 서버 세션이 복귀하고, 그 뒤엔 새 세션이다.
  연속성은 리포 문서로만 담보한다.
- 폰 프롬프트에서 **"항상 허용"을 누르지 않는다.** settings의 `allow`에 영구 기록되어
  승인 게이트가 사라진다. `~/.claude/settings.json`의 `permissions.allow`를 주기적으로
  감사한다.

정지는 텔레그램 `/stop`(또는 `/down <p>`)으로 직접 한다 — 자동 유휴 정지는 없다
(아래 "수동 정지" 참고).

## 인증 문제 진단

v5부터 **claude.ai OAuth(`claude auth login` → `~/.claude/.credentials.json`)만** 쓴다.
`claude setup-token`이 발급하는 `CLAUDE_CODE_OAUTH_TOKEN`은 모델 요청 전용이라
Remote Control 세션을 만들 수 없고, `~/.claude/.env`에 남아 있으면 "API-key auth takes
precedence"로 서버 기동 자체가 거부된다. `/status`가 이 두 경우를 구분해 보여준다.

| `/status` 표시 | 원인 | 조치 |
|---|---|---|
| `.env 에 CLAUDE_CODE_OAUTH_TOKEN 이 남아 있어요` | 토큰 잔존 | `.env`에서 줄 제거 후 `sudo systemctl restart claude-rc@<프로젝트>` |
| `claude.ai 로그인이 없어요` | `.credentials.json` 없음 | `claude auth login` (대화형, SSM 셸) |
| `401 ...` 화면 스캔 | 리프레시 토큰 만료 | `claude auth login` 후 재시작 |

### 1단계 — 로그인 상태

```bash
sudo -u ubuntu bash -c 'export HOME=/home/ubuntu PATH=/home/ubuntu/.local/bin:$PATH; claude -p "Reply with exactly: PONG"'
grep -c CLAUDE_CODE_OAUTH_TOKEN /home/ubuntu/.claude/.env || true   # 0 이어야 한다
```

`PONG`이 나오고 grep이 0이면 인증은 정상이다. 2단계로 간다.

### 2단계 — 서버 세션은 살아 있는가

```bash
systemctl status claude-rc@<프로젝트>
sudo -u ubuntu tmux -L rc-<프로젝트> capture-pane -t rc-<프로젝트> -p | tail -20
journalctl -u claude-rc@<프로젝트> -n 30
```

화면에 `Enable Remote Control?`이 떠 있으면 계정 단위 1회 수락이 안 된 것이다 —
`docs/SETUP.md` 9단계.

### 함정 A — 만료된 `.credentials.json`이 토큰을 가린다

v5부터는 `.env`에 토큰이 없으므로 이 가드는 보통 발동하지 않는다. 토큰을 다시 넣으면
그때 살아난다. 코드는 `claude-rc-wrap.sh`에 남겨 두었다.

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
sudo systemctl restart claude-rc@<프로젝트>
```

**자동화되어 있다.** `ec2/claude-rc-wrap.sh`가 기동 시 만료 여부를 확인해 만료된
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

**자동화되어 있다.** `claude-rc-wrap.sh`가 기동 시 데몬 시작 시각과 `.credentials.json`
mtime(파일이 없으면 `.env`로 폴백)을 비교해, 자격증명이 더 최신일 때만 데몬을 정리한다.
데몬이 더 최신이면 건드리지 않으므로 일반 재시작에서는 진행 중인 bg 작업이 살아남는다.

### 재로그인

1단계에서 실제로 로그인이 죽었다고 확인된 경우에만 한다.

```bash
claude auth login           # 브라우저 인증 필요. 봇이 대신 못 한다
sudo systemctl restart claude-rc@<프로젝트>
```

## 서비스 관리

```bash
systemctl list-units 'claude-rc@*' --all
systemctl restart claude-rc@<프로젝트>
journalctl -u claude-rc@<프로젝트> -n 50
sudo -u ubuntu tmux -L rc-<프로젝트> ls          # 유닛마다 소켓 rc-<p>, 세션 이름도 rc-<p>
grep claude-rc-wrap /var/log/syslog | tail      # 가드 동작 기록
systemctl show claude-rc.slice -p MemoryCurrent,MemoryHigh,MemoryMax
```

`claude-rc@.service`는 프로젝트 단위 템플릿이다. `pocket up <p>`이 enable + start를,
`pocket down <p>`이 disable + stop을 같이 한다 — 부팅 시 자동 기동 여부도 그때 같이
바뀐다(스티키). 각 인스턴스는 `claude-rc-wrap.sh <p>`가 프로젝트 실제 경로를
`realpath`로 풀어 tmux 세션 `rc-<p>`를 만들고 블록한다. 세션이 죽으면 래퍼가 1로
종료하고 `Restart=on-failure`가 10초 뒤 되살린다. `ExecStop`은 tmux 세션을 죽인다.
래퍼는 Lambda·`pocket`의 검증과 별개로 세션 이름을 `^[a-z0-9-]{1,24}$`로 직접
검증해 형식이 어긋나면 1로 종료한다.

`ExecStart`에서 `tmux new-session -d`를 직접 부르면 안 된다. tmux 서버가 스스로
데몬화해 systemd가 PID를 놓치고 재시작 루프가 돈다. 래퍼는 이걸 피하려고 있다.

모든 `claude-rc@*` 인스턴스는 `claude-rc.slice`로 묶여 합산 메모리 한도
(`MemoryHigh=3G`, `MemoryMax=3.5G`)를 공유한다 — 한 프로젝트가 아니라 박스 전체
기준이다. 넘으면 먼저 느려지고(`MemoryHigh`), 그래도 넘으면 slice 안 프로세스가
종료된다(`MemoryMax`). SSM 에이전트가 메모리 부족으로 죽어 원격 복구 길이 끊기는
것을 막기 위한 상한이다.

ubuntu 사용자(각 프로젝트 세션 포함)는 sudoers(`/etc/sudoers.d/claude-rc`)로
`systemctl start|stop|restart|enable|disable|reset-failed claude-rc@*`만 실행할 수
있다. 어느 세션이든 다른 프로젝트 서버를 켜고 끌 수 있다는 뜻이다 — 1인 사용이라
받아들인 경계다.

## 수동 정지

**자동 유휴 정지는 쓰지 않는다** (2026-09-23 결정). 대화가 없는 Remote Control
서버도 60초마다 CPU를 약 1초씩 써서, CPU 기반 유휴 감시가 매번 "활동"으로 오판했다
(실측: [범용화 설계 문서](superpowers/specs/2026-09-23-generalize-multi-project-design.md) F12).
정지는 텔레그램 `/stop`(전체) 또는 `/down <p>`(프로젝트 하나)로 직접 한다. 박스가
켜진 채 잊히면 t3.medium 기준 하루 약 $1.3이 든다는 점만 감수한다 — `/status`가
가동 시간을 보여주므로 잊었는지는 거기서 확인한다.

`ec2/idle-watch.sh`와 `tests/test_idle_watch.sh`는 지우지 않고 남겨 뒀다.
`ec2/install.sh`는 cron에 `idle-watch.sh`를 **등록하지 않고**, 기존 등록이 있으면
지운다.

되살리고 싶어지면 두 방법이 있다 (상세는
[범용화 설계 문서](superpowers/specs/2026-09-23-generalize-multi-project-design.md)의
"5-8. 유휴 감시" 절):

1. **깜빡이 방지용 상한.** "부팅 후 N시간이면 무조건 정지". 판정이 없어 오판도 없다.
2. **상태 기반 판정.** `claude agents --json`으로 세션마다 `status`·`waitingFor`를
   직접 물어본다(실측으로 동작 확인됨). CPU·트랜스크립트 mtime은 보조 신호로만 쓴다.

로컬 테스트: `bash tests/test_idle_watch.sh` (가짜 `~/.claude` 트리, AWS 불필요).

## 배포

### EC2 스크립트

`ec2/` 아래 파일(`pocket`, `claude-rc@.service`, `claude-rc.slice`, `claude-rc-wrap.sh`,
`claude-rc.sudoers`, `worktree-env-hook.sh`, `idle-watch.sh`, `install.sh`,
`claude-settings.json`, `secrets.env.example`, `telegram.env.example`)과 리포 루트의
`ec2-claude-md-patch.md`를 `/tmp`로 보내고 `sudo bash /tmp/install.sh`를 실행한다.
멱등하므로 여러 번 돌려도 된다. 박스를 v5에서 올리는 경우 설치 뒤
`sudo bash /tmp/migrate-v6.sh <이름>=<경로> …`를 한 번 더 돌린다(이행, 멱등,
리포를 옮기지 않는다).

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
`update-function-configuration`은 전체를 덮어쓰므로 항상 셋을 함께 넘긴다. 텔레그램
봇 명령 메뉴(`setMyCommands`)도 `/projects /up /down /trees`를 반영해 갱신한다.

배포 전에 테스트를 돌린다. 순수 헬퍼는 AWS 없이 검증된다.

```bash
python3 -m unittest discover -s tests
bash tests/test_claude_rc_wrap.sh
bash tests/test_worktree_env_hook.sh
bash tests/test_idle_watch.sh
```

### 롤백

`backup/lambda_function.v3.py`는 인스턴스 ID가 하드코딩되어 있어 gitignore 대상이다.
로컬에만 있다.

```bash
mkdir -p /tmp/rb && cp backup/lambda_function.v3.py /tmp/rb/lambda_function.py
(cd /tmp/rb && zip -q rollback.zip lambda_function.py)
aws lambda update-function-code --function-name ec2-boot-manager --zip-file fileb:///tmp/rb/rollback.zip
```

EC2 쪽은 되돌릴 필요가 없다. systemd 유닛은 이전 버전과 공존 가능하다.

더 이전 버전(v4 이하)으로 되돌리려면 `backup/`의 해당 버전 파일을 같은 방법으로
올린다. v4는 `claude-telegram.service`를 다시 설치해야 하는 등 EC2 쪽 구조 자체가
다르므로, 그럴 정도의 롤백이 필요하면 이 문서의 이전 커밋을 함께 참조한다.

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

- **대화형 1회 작업이 있다** — `claude auth login`, "Enable Remote Control?" 계정 단위
  수락은 SSM 셸에서 사람이 해야 한다. 봇이 대신 못 한다. 워크스페이스 신뢰는
  `pocket trust <p>`가 자동화하지만, 실패하면(경쟁 상태 등) 역시 SSM 셸에서 직접 한다
- **`claude doctor`는 TTY 없이 실행하면 블록된다** — SSM에서 호출하지 않는다.
  `/status`는 `.env` 토큰 잔존·`.credentials.json` 존재·화면 401 스캔으로 대신한다
- **동시 세션 수 = 디스크·메모리** — 프로젝트당 `1 + POCKET_SESSIONS`(기본값 2라
  실제로는 3), 동시에 켤 수 있는 서버는 `pocket`의 `POCKET_MAX_SERVERS`(기본 2)까지.
  `pocket up`은 디스크 여유가 `POCKET_MIN_FREE_GB`(기본 3GB) 미만이면 거부한다
- **앱이 만든 워크트리는 의존성을 설치하지 않는다** — 세션에 들어가 필요할 때 시킨다.
  `.env` 등 git 무시 파일만 `SessionStart` 훅이 자동으로 복사한다
- **sudoers는 정규식 규칙** — `^(start|stop|restart|enable|disable|reset-failed)
  claude-rc@[a-z0-9][a-z0-9-]{0,23}\.service$`라서 인자는 정확히 `claude-rc@<name>.service`
  하나만 받는다(글로브 `*`는 공백까지 매칭해 유닛을 덧붙일 수 있었다). sudo 1.9.10+
  문법이고 `install.sh`가 설치 전 `visudo -cf`로 검증한다
- **로컬에 session-manager-plugin이 없으면** `aws ssm start-session`을 못 쓴다.
  모든 인스턴스 작업을 `aws ssm send-command`로 해야 한다
- **`ssm_run`의 25초 상한** — SSM이 느려지면 `/status`가 30초 한계에 근접할 수 있다.
  Duration이 20,000ms를 넘기 시작하면 상한을 낮추고 부분 결과를 반환하도록 바꾼다

# pocket-claude v6 — 범용화 (멀티 프로젝트) 설계안

작성 2026-09-23 새벽, 사용자 취침 중 자율 작성. 상태: **사용자 검토 대기, 구현 시작 안 함.**
선행 문서: [v5 Remote Control 설계](2026-09-22-remote-control-mode-design.md).
근거 사실은 [부록 A](#부록-a-확인한-사실), 독립 리뷰 반영 내역은 [부록 B](#부록-b-리뷰-반영-기록).

## 한 줄 요약

"EC2 한 대에서 **여러 리포**의 Claude Code 세션을 폰으로 켜고 끄는 도구"로 바꾼다.
추천안은 **프로젝트마다 Remote Control 서버 하나**이고, 작업 세션(워크트리)은 폰 앱의 "새 세션"이 만든다. 봇은 프로젝트 단위로 켜고 끄기만 한다. 공개 리포에는 특정 프로젝트 정보가 남지 않는다.

---

## 0. 아침에 먼저 보실 것

### 급한 것 두 가지 — 둘 다 정리됨 (범용화와 무관)

1. ~~**관리자 AWS 키가 박스에 있습니다.**~~ **완료: 2026-09-23 박스의 `~/.aws/credentials` 삭제, 인스턴스 역할 정책을 실사용 기준으로 조정(PR #2).** 유출 증거는 없었습니다. 박스 `~/.aws/credentials`에 관리자 사용자의 액세스 키가 있고, 그 사용자의 키는 하나뿐이라 맥 CLI가 쓰는 키와 같습니다. 2026-09-23 감사 결과: CloudTrail 90일 기록의 사용처는 맥·본인 다른 회선·EC2 자신뿐(낯선 출처 0), 박스의 대화 기록에 키 ID·비밀값이 나온 적 0건, 저장소 히스토리에도 없음. 문제는 실제 유출이 아니라 **상시 위험**입니다. 폰에서 여는 세션은 `cat`으로 이 파일을 읽을 수 있고(settings의 deny는 Read 도구만 막고 Bash는 못 막음), 세션 기록은 Anthropic 서버에 저장됩니다. 박스는 인스턴스 역할만으로 필요한 AWS 작업이 되므로 이 파일은 없어도 됩니다.
   - 권장(최소): **박스의 `~/.aws/credentials` 삭제.** 맥 키는 그대로 둔다
   - 선택(철저): 키 교체 — 새 키 발급 → 맥 교체 → 옛 키 삭제. 세션이 파일을 읽은 흔적이 없으므로 필수는 아니다
   - 삭제 후 인스턴스 역할에 없는 권한(예: `ec2:DescribeInstances`)을 쓰는 작업은 실패하므로, 필요하면 operator 정책에 추가한다
2. ~~유휴 자동 정지가 동작하지 않습니다.~~ **해결: 자동 정지를 쓰지 않기로 했습니다(2026-09-23).** 대화가 없는 Remote Control 서버도 CPU를 분당 약 1초씩 써서 유휴 감시가 매번 "활동"으로 판정했는데, 정지는 `/stop`으로 직접 하기로 정했습니다. cron 등록만 제거하면 됩니다(§5-8).

### 결정해 주실 것

| # | 질문 | 추천 |
|---|---|---|
| 1 | 조합안 A / B / C (§4) | **B** |
| 2 | 서버가 기동 시 본 체크아웃에 만드는 "메인 세션"을 둘까 (§5-2) | 둔다. v5의 `ops` 역할 |
| 3 | 리포를 `~/work`로 옮길까, 링크만 걸까 (§5-1) | **링크만**. 옮기면 신뢰·대화 기록이 끊김 |
| 4 | 부팅 시 무엇을 띄울까 (§3 결정 5) | 마지막에 켜 둔 것 (스티키) |
| 5 | ~~관리자 키~~ 처리 완료. 키 자체 교체까지 할까 | 선택. 유출 흔적이 없어 필수는 아님 |
| 6 | `/add`(clone)를 누구 리포까지 허용할까 (§5-3) | 본인 GitHub 계정 리포만 |
| 7 | 메모리 한도를 넘으면 느려지게 할까, 죽일까 (§5-7) | 3GB부터 느리게, 3.5GB에서 종료 |
| 8 | 공개 리포의 목표 (§7 단계 3) | 내 용도 우선, 포크 지원은 나중 |

"B로 가자"만 주시면: 급한 것 두 가지 처리 → §8 실측 30분 → 단계 1 구현 계획 순서로 갑니다.

---

## 1. 지금 gamer4info에 묶인 곳

- **Lambda**: 리포 경로·워크트리 경로·세션 접두사 `gamer4-`·예약 이름 `ops` 상수, 이를 쓰는 20여 곳. `/new`가 `git -C ~/gamer4info worktree add`를 직접 실행
- **래퍼** `claude-rc-wrap.sh`: 같은 경로 상수, `--name "gamer4-<이름>"`
- **유닛·설정 경로**: `~/.config/gamer4/` (secrets·telegram), settings의 deny 경로
- **settings 템플릿**: ask 규칙이 gamer4info 명령 기준 (`sb_sql.py`, `yarn deploy:lambda` 등)
- **IAM 템플릿** `iam/gamer4-operator-policy.json`: `gamer4-sync` 스택·함수 이름이 박혀 있음
- **install.sh**: `claude-rc@ops` enable
- **문서**: SETUP 14곳, README 5곳, EC2 CLAUDE.md 패치

박스에는 이미 `guam-go`, `devlog-v2` 리포가 있지만 지금 구조로는 거기서 세션을 띄울 방법이 없습니다.

## 2. 목표와 성공 기준

1. **프로젝트 추가에 코드 수정·재배포가 없다.** 단계 1 기준으로 "clone + 신뢰 한 번 + 필요한 `.env` 배치"로 끝난다. 단계 3의 `/add`가 들어오면 폰 명령 하나.
2. 공개 리포의 코드·템플릿에 특정 프로젝트 이름이 없다 (역사 문서 제외).
3. v5 기능 유지: 전원, 상태, 세션 생성·정지·정리, 폰 승인 게이트, 푸시 알림. (유휴 자동 정지는 §5-8 결정에 따라 뺀다.)
4. 보안은 v5보다 넓어지지 않는다: 이름 검증 2중 이상, sudoers 최소화, **Lambda는 Claude를 직접 띄우지 않는다**, 저장소 민감정보 0.

범위 밖: EC2 여러 대, 사용자 여러 명, 웹 대시보드.

---

## 3. 결정별 선택지

### 결정 1 — 세션 모델 (가장 큰 결정)

**1-B 프로젝트 서버 (추천).** 유닛 하나 = 프로젝트 하나. 작업 세션은 앱의 "새 세션"이 만들고, `--spawn worktree`가 `<리포>/.claude/worktrees/<이름>/`에 워크트리를 자동으로 판다(F3).
- 장점: 봇 명령이 절반으로 준다. 워크트리 생성·신뢰 상속(F1)·커밋 보호를 제품이 한다.
- 단점: 워크트리 위치·브랜치 이름(`worktree-<이름>`)을 Claude Code가 정한다. 워크트리가 쌓이므로 정리 기능이 단계 1부터 필요하다(§5-5).

**1-A 봇이 워크트리 관리 (v5 확장).** 유닛 하나 = 세션 하나. `/new <프로젝트> <브랜치>`.
- 장점: 브랜치 이름을 직접 정한다. v5와 같은 감각.
- 단점: 모든 명령에 인자 두 개. 봇이 git 레이아웃을 알아야 한다.

브랜치 이름이 중요하면 세션 안에서 "feat/x로 옮겨줘"라고 말하는 쪽이 간단하다.

### 결정 2 — 프로젝트 등록

**2-A 관례 (추천).** `~/work/<이름>`이 있으면 프로젝트다. 실제 폴더든 **다른 곳을 가리키는 링크**든 된다(§5-1). 설정 파일이 없고, 링크를 지우면 목록에서 사라진다.
- 대가: 목록이 박스에 있으므로 **박스가 꺼져 있으면 `/projects`가 목록을 못 보여준다**("EC2 꺼져 있음 — /start"로 응답).

**2-B 등록 파일.** `projects.conf`에 `이름=경로`. 링크 방식과 효과가 같아 이점이 없다.

**2-C AWS에 목록 저장**(Lambda 환경변수·SSM Parameter). 박스가 꺼져도 목록을 보지만 추가할 때마다 AWS 설정을 바꿔야 한다.

프로젝트 이름 규칙은 v5와 같게 소문자·숫자·하이픈 24자 이내. 프로젝트별로 다른 값(동시 세션 수, `AWS_PROFILE`)은 선택 파일 `~/.config/pocket-claude/projects/<이름>.env`에 둔다.

### 결정 3 — 텔레그램 명령 체계

**3-A 인자 + 인라인 버튼 (추천).** `/projects`가 목록과 [▶ 켜기] [⏹ 끄기] [🌳 트리] 버튼을 준다. 타이핑하려면 `/up guam-go`.

**3-B 네임스페이스** `/p up guam-go`. 폰에서 두 단계 문법이 번거롭다.

**3-C "현재 프로젝트" 상태** `/use guam-go` 후 `/up`. Lambda가 상태를 저장해야 하고 무엇이 현재인지 헷갈린다.

추천안 명령:

| 명령 | 동작 |
|---|---|
| `/start` · `/stop` | EC2 켜기·끄기. 켜면 "켜 둔" 프로젝트들이 자동으로 뜬다 |
| `/status` | EC2, 프로젝트별 서버, 메모리·디스크, 워크트리 수, 인증 |
| `/projects` | 목록(상태·브랜치·마지막 대화) + 버튼 |
| `/up <p>` · `/down <p>` | 서버 켜기·끄기. 부팅 시 자동 여부도 같이 바뀐다 |
| `/trees <p>` | 세션 워크트리 목록(미커밋·미푸시 표시) + [정리] 버튼 |
| `/add <repo> [이름]` | clone + 신뢰 (단계 3, 본인 리포만) |

```
/projects
→ 🟢 gamer4info  [main]  3분 전   워크트리 2
     [⏹ 끄기] [🌳 트리]
  ⚪ guam-go     [main]  2일 전
     [▶ 켜기]
```

### 결정 4 — 제어 로직의 위치

**4-A Lambda가 셸 스크립트를 만든다 (v5).** 프로젝트가 늘수록 Lambda가 박스 사정을 너무 많이 안다.

**4-B 박스의 `pocket` CLI + 얇은 Lambda (추천).** Lambda는 SSM으로 `pocket <동사> <인자> --json`만 부르고 결과를 문장·버튼으로 바꾼다. 로직이 박스 한 곳에 모이고 거기서 테스트된다. SSM 셸과 허브 세션(안 C)도 같은 CLI를 쓴다.

**4-C 4-B + 전용 SSM 문서 (단계 2).** `AWS-RunShellScript` 대신 `PocketCommand` 문서를 쓴다. 동사는 `allowedValues`, 인자는 `allowedPattern`으로 **AWS가 박스 도달 전에 검증**하고(F8), Lambda 역할은 이 문서만 실행할 수 있다. 지금은 Lambda 역할이 박스에서 **root 셸을 아무거나** 돌릴 수 있는데 이걸 없앤다.

### 결정 5 — 부팅 시 무엇을 띄우나

**5-A 스티키 (추천).** `/up` = enable + start, `/down` = disable + stop. 재부팅하면 마지막에 켜 둔 것들이 돌아온다.

**5-B 설정 목록.** 명시적이지만 폰에서 못 바꾼다.

**5-C `/start <p>`.** Lambda가 인스턴스 태그에 기록하고 부팅 서비스가 읽어 기동. 필요해지면 5-A 위에 얹는다.

### 결정 6 — 권한 게이트와 자격증명

- **승인 게이트**: 사용자 설정 → **`/etc/claude-code/managed-settings.json`** (root 소유, 세션이 못 바꿈). managed의 `ask`는 사용자 설정의 `allow`나 폰의 "다시 묻지 않기"로 꺼지지 않는다(F4). `defaultMode`도 여기서 고정해 v5에서 겪은 `auto` 전환(F5)을 막는다. 프로젝트 특화 ask는 각 리포의 `.claude/settings.json`.
- **AWS**: 박스의 관리자 키 파일 삭제(§0). 인스턴스 역할은 모든 프로젝트 작업의 **합집합 권한 하나**로 운영한다. 모든 서버가 같은 `ubuntu` 사용자·같은 인스턴스 역할로 돌기 때문에 프로젝트별 역할(F9)을 만들어도 한 세션이 다른 프로젝트 프로필을 쓸 수 있다. 프로젝트별 프로필은 **실수 범위를 줄이는 용도**일 뿐 보안 경계가 아니다. 필요할 때만 쓴다.
- **비밀**: 공통 비밀 파일은 두지 않는다. gh·vercel은 각 CLI 로그인 파일을 쓴다. 프로젝트 전용 값만 `projects/<p>.env`.
- **세션 격리 (단계 2, 선택)**: `claude remote-control --sandbox` + managed `sandbox.filesystem.denyRead`에 `~/.aws`, `~/.config/pocket-claude`, `~/.claude/.credentials.json`(F13). OS 수준이라 세션이 띄운 모든 하위 프로세스에 적용된다. 다른 프로젝트 폴더 읽기까지 막을 수 있는지는 실측 필요.
- **sudoers**: 어느 세션이든 다른 프로젝트 서버를 켜고 끌 수 있다. v5부터 그랬고 1인 사용이라 받아들인다.

---

## 4. 조합안

**안 A 최소 변경** (1-A, 2-B, 3-A, 4-A, 5-B). v5 상수를 설정 파일로 빼고 명령에 프로젝트 인자를 더한다. 약 1일. 봇 명령 8개에 대부분 인자 두 개. 워크트리 수명을 봇이 계속 관리한다.

**안 B 프로젝트 허브 (추천)** (1-B, 2-A, 3-A, 4-B→4-C, 5-A, 결정 6 추천). 프로젝트 = 서버, 세션은 앱이 만들고, 로직은 `pocket` CLI. 단계별 2~3일. 버튼으로 거의 타이핑 없음. `--spawn worktree` 실동작 확인이 먼저 필요(§8).

**안 C 허브 세션** (안 B + 항상 켜진 허브 세션). 폰에서 허브 세션에 "guam-go 켜줘"라고 말하면 허브가 `pocket up guam-go`를 실행한다. 텔레그램은 `/start /stop /status`만. 안 B + 반나절. 제어가 LLM과 승인 프롬프트에 의존하고, 허브가 죽으면 텔레그램으로 복구해야 한다. 안 B의 CLI가 있으면 CLAUDE.md 한 단락으로 얹을 수 있어 **나중에 선택**하면 된다.

**검토 후 기각: `~/work`에서 서버 하나.** 리포 밖 폴더라 신뢰는 저장되고 기동 시 검사만 통과하면 모든 리포에 닿는다. 하지만 프로젝트의 `.claude/settings.json`·CLAUDE.md·훅·워크트리·세션 복귀가 모두 **기동 폴더 기준**이라 프로젝트 맥락이 사라진다. 안 C의 열화판이다. `--spawn session` 모드는 문서에 설명이 없어 쓰지 않는다.

---

## 5. 안 B 상세

### 5-1. 박스 레이아웃

```
/home/ubuntu/work/                     ← 프로젝트 목록 (링크 모음)
  gamer4info -> /home/ubuntu/gamer4info
  guam-go    -> /home/ubuntu/workspace/guam-go
/home/ubuntu/gamer4info/               ← 실제 리포 (옮기지 않음)
  .claude/worktrees/<이름>/             ← 앱이 만든 세션 워크트리
/home/ubuntu/.config/pocket-claude/
  telegram.env                         ← 부트봇 알림용. 수동 정지 결정으로 현재 미사용
  projects/<p>.env                     ← 선택: 동시 세션 수, AWS_PROFILE
/home/ubuntu/bin/pocket                ← CLI
/etc/claude-code/managed-settings.json ← 공통 승인 게이트 (단계 2)
```

**왜 링크인가.** 신뢰(F1)와 대화 기록(`~/.claude/projects/<경로>`), 세션 복귀는 모두 **실제 경로**에 묶여 있다. 리포를 옮기면 셋 다 끊긴다. 래퍼와 CLI는 링크를 `realpath`로 풀어서 그 경로에서 Claude를 띄우므로 신뢰 키가 둘로 갈라지지 않는다.

### 5-2. 유닛, 래퍼, 메인 세션

- 유닛 `claude-rc@<p>`: `EnvironmentFile=-/home/ubuntu/.config/pocket-claude/projects/%i.env`, `Slice=claude-rc.slice`. `%h`는 `/root`가 되므로(F10) 경로는 모두 절대경로
- 래퍼 `claude-rc-wrap.sh <p>`: 이름 검증 → `realpath ~/work/<p>` → 신뢰 확인 → tmux 소켓 `rc-<p>`에서
  `claude remote-control --name "<p>" --spawn worktree --capacity $((1 + ${POCKET_SESSIONS:-2})) --permission-mode default --no-chrome`
- **메인 세션**: 서버는 기동하면 본 체크아웃에 세션 하나를 미리 만든다(`--create-session-in-dir` 기본값, F14). 이 세션은 워크트리가 아니라 **메인 브랜치를 직접 만진다.** v5의 `ops`처럼 운영·문서용 세션으로 두고, 코드 작업은 앱에서 "새 세션"으로 워크트리를 만든다. 이 세션도 동시 세션 수에 들어가므로 `--capacity`는 1 + 작업 세션 수. 끄는 방법(`--no-create-session-in-dir`)도 있지만 그러면 서버 정지 시 세션이 보관 처리되어 4시간 복귀가 없다
- sudoers: `^(start|stop|restart|enable|disable|reset-failed) claude-rc@[a-z0-9][a-z0-9-]{0,23}$`

### 5-3. 신뢰와 새 프로젝트

**신뢰 확인.** `pocket up <p>`은 기동 전에 `~/.claude.json`에서 실제 경로의 `hasTrustDialogAccepted`를 확인한다. 없으면 기동하지 않고 "신뢰 필요"를 돌려준다(신뢰 없이 띄우면 에러 종료 → 재시작 루프, F2).

**`pocket trust <p>`.** v5 설치 때 박스에서 실제로 쓴 절차를 자동화한다.
1. 별도 tmux 소켓에서 실제 경로로 대화형 `claude` 실행
2. 화면에 "Yes, I trust this folder"가 보이면 `Down`, `Enter`
3. "Settings Warning" 화면이 나오면 `Enter`
4. `/exit` 입력
5. `~/.claude.json`을 다시 읽어 키를 확인하고, 없으면 한 번 재시도

실행 중인 다른 Claude 프로세스도 `~/.claude.json`을 다시 쓰므로 경쟁이 생길 수 있다. 5번 확인이 그 안전장치다. 화면 문구에 의존하므로 CLI 버전이 바뀌면 깨질 수 있는 부분이다.

**`/add <repo>` (단계 3).** clone 직후 신뢰까지 하므로 신뢰 대화상자의 목적(낯선 리포의 훅·CLAUDE.md를 보고 판단)을 건너뛴다. 그래서 **Lambda 환경변수의 본인 GitHub 계정 리포만** 허용하고, SSM 문서 패턴에도 같은 제한을 둔다.

### 5-4. 워크트리 세션 준비

앱이 만든 워크트리에는 `.env`가 없다(F3). 공통 훅이 "지금 폴더가 워크트리이고, 본 체크아웃에 있는 git 무시 파일(`.env*`, `.vercel/`)이 여기 없으면 복사"한다.
- 1순위 `SessionStart` 훅. 단 Claude가 **편집 직전에야** 세션을 워크트리로 옮기는 경우가 있어(F3 인용) 훅이 본 체크아웃에서 돌 수 있다. §8에서 확인
- 대안 `WorktreeCreate` 훅. 워크트리 생성을 대신하므로 `git worktree add`와 복사를 직접 하고 경로를 출력해야 한다
- 어느 쪽이든 훅은 **아무것도 출력하지 않는다**(출력은 세션 맥락에 들어간다). 의존성 설치는 디스크를 0.8GB씩 쓰므로 자동으로 하지 않는다

### 5-5. 워크트리 정리 (단계 1)

앱의 "새 세션"마다 체크아웃이 하나씩 생기고, `yarn install`을 하면 0.8GB가 더 든다. `/up`의 디스크 확인은 앱에서 세션을 만들 때는 걸리지 않으므로 정리가 단계 1부터 필요하다.
- `/status`·`/projects`에 디스크 여유와 프로젝트별 워크트리 수를 표시
- `pocket prune <p>`는 다음을 **모두** 만족하는 워크트리만 지운다
  - 미커밋 변경 없음 (`git status --porcelain` 비어 있음)
  - 미푸시 커밋 없음 (upstream이 있으면 `@{u}..HEAD`, 없으면 `origin/HEAD..HEAD`가 비어 있음). `git worktree remove`는 미커밋만 막아 주므로 직접 검사해야 한다
  - 그 폴더를 작업 디렉터리로 쓰는 Claude 프로세스 없음
- 지운 워크트리의 `worktree-<이름>` 브랜치는 병합된 경우에만 지운다

### 5-6. Lambda와 박스 사이 경계

- `pocket`은 JSON을 고정 구분 줄 사이에 한 번 출력한다. Lambda는 마지막 완전한 JSON 블록만 파싱하고, 없으면 실패로 처리한다
- SSM 표준 출력은 24,000자에서 잘린다. 잘리면 실패로 보고한다(오보 방지)
- 버튼을 누르면 **SSM 호출 전에** `answerCallbackQuery`를 먼저 부른다. 늦으면 로딩 표시가 남고, 30초를 넘기면 API Gateway 오류 → 텔레그램 재전송 → `/up` 중복 실행으로 이어진다
- 버튼 인증은 `callback_query.from.id`를 허용 ID와 비교한다. 메시지 명령과 같은 검사
- `callback_data`는 `up:guam-go`처럼 64바이트 이내(F11). 버튼 결과는 `editMessageReplyMarkup`으로 같은 메시지를 갱신

### 5-7. 자원 한도

t3.medium(4GB) 기준 실측·추정: 유휴 서버 약 150MB, 대화 중 세션 최대 약 500MB, 리포 하나 `yarn install` 후 약 0.8GB 디스크.
- `claude-rc.slice`에 합산 `MemoryHigh=3G`(넘으면 느려짐), `MemoryMax=3.5G`(넘으면 slice 안 프로세스 종료). SSM 에이전트가 메모리 부족으로 죽어 원격 복구 길이 끊기는 것을 막는다. `next build` 중에는 느려져 멈춘 것처럼 보일 수 있다
- 동시 서버 최대 2개, 디스크 여유 3GB 미만이면 `/up` 거부
- 서버 세 개 이상을 상시로 쓰려면 t3.large(8GB) + EBS 40GB

### 5-8. 유휴 감시 — 제거 (2026-09-23 사용자 결정: 수동 정지)

**결정: 자동 유휴 정지를 쓰지 않는다.** 정지는 텔레그램 `/stop`으로 직접 한다. 박스가 켜진 채 잊히면 t3.medium 기준 하루 약 $1.3이 든다는 점만 감수한다.

할 일: cron의 `idle-watch.sh` 등록 제거(스크립트와 테스트는 남겨 둔다), `/status`에 **가동 시간**을 눈에 띄게 표시해 켜져 있다는 사실을 상기시킨다. `telegram.env`는 더 이상 쓰이지 않지만 다시 켤 때를 위해 둔다.

되살리고 싶어지면 두 가지 방법이 있다.

1. **깜빡이 방지용 상한.** "부팅 후 N시간이면 무조건 정지". 판정이 없어 오판도 없고, 작업 중이어도 끊긴다. EventBridge 일정으로 계정 쪽에서 걸 수도 있다
2. **상태 기반 판정.** 아래 방식. 지금까지의 신호(화면 해시 → CPU 변화량)는 둘 다 바깥에서 추측하는 방식이라 틀렸고, Claude Code가 세션 상태를 직접 알려주는 명령이 있다(F15).

```
claude agents --json
→ 세션마다 cwd, kind, status, waitingFor(permission prompt / input needed …), state
```

**새 판정 규칙 (주 신호).**
- 어느 세션이든 작업 중이면 활동 → 카운터 리셋
- 모든 세션이 대기 상태면 유휴. **사람 입력을 기다리는 세션(`waitingFor`)도 유휴로 센다** — v5가 백그라운드 작업의 `blocked`를 종료 상태로 본 것과 같은 이유다. 60분 동안 답이 없으면 그건 사람이 자리를 뜬 것이다
- 명령이 실패하거나 느리면 "알 수 없음"으로 보고 아래 보조 신호로 판단한다

**보조 신호 (보험).** 트랜스크립트 mtime, `~/.claude/jobs`의 비종료 작업, `claude-rc.slice` cgroup CPU가 5분에 60초(한 코어의 20%) 이상. CPU는 더 이상 주 신호가 아니다.

   이 방식을 쓸 때만 §8-6(세션 상태 출력 실측)이 필요하다.

### 5-9. 테스트

- `pocket`: `unittest` + 임시 디렉터리 + PATH의 가짜 `systemctl`·`git`·`gh`·`claude`
- Lambda: 버튼 콜백, JSON 경계(잘림·쓰레기 출력), 새 동사 검증
- 래퍼·idle-watch: 기존 bash 테스트 확장 (cgroup 경로를 환경변수로 대체)
- 박스 스모크: 프로젝트 두 개 동시 기동, 앱에서 새 세션 → 워크트리·`.env` 확인, `prune`의 미푸시 보호, 재부팅 후 스티키 복원, 유휴 60분 정지

---

## 6. 이행 (v5 → v6)

1. `~/work/`를 만들고 `gamer4info` 링크를 건다. 리포는 옮기지 않으므로 신뢰·기록·복귀가 그대로다
2. `guam-go`, `devlog-v2`도 원하면 링크 + `pocket trust`
3. `claude-rc@ops` disable → `claude-rc@gamer4info` enable. 실제 경로가 같아 4시간 복귀 기록이 이어질 수 있다(실측). 앱의 세션 이름은 `gamer4-ops`에서 `gamer4info`로 바뀐다
4. `~/.config/gamer4/` → `~/.config/pocket-claude/`. settings의 deny 경로도 갱신
5. `iam/gamer4-operator-policy.json`은 스택·함수 이름을 자리표시자로 바꾼 `iam/operator-policy.example.json`으로
6. Lambda 재배포, 봇 명령 메뉴 갱신

## 7. 단계 계획

| 단계 | 내용 |
|---|---|
| **0. 즉시** | ~~박스의 관리자 키 파일 삭제~~ 완료, idle-watch cron 제거 (수동 정지 결정) |
| **1. 범용화 코어** (1.5~2일) | `pocket` CLI(`list status up down trust trees prune`), `~/work` 링크 관례, 유닛·래퍼·slice, 메인 세션 + capacity, 워크트리 `.env` 훅, Lambda `/projects /up /down /trees /status` + 버튼 + JSON 경계, 이행, 문서·IAM 템플릿에서 gamer4 제거 |
| 2. 보안 강화 (0.5~1일) | managed settings 게이트·`defaultMode` 고정, `PocketCommand` SSM 문서 + Lambda 역할 축소, 샌드박스 denyRead(선택) |
| 3. 편의 (선택) | `/add`(본인 리포만), 프로젝트 `.env`를 맥에서 SSM Parameter Store로 전달하는 스크립트, 허브 세션(안 C), 포크용 SAM 템플릿 |

각 단계는 따로 스펙 → 계획 → 구현을 거친다.

## 8. 구현 전에 박스에서 확인할 것 (30분)

문서로 확정하지 못한 것만 남겼습니다. 앱 화면을 봐야 해서 함께 합니다.

1. `--spawn worktree` 실동작: 앱의 새 세션 → 워크트리 위치·브랜치 이름, 세션 종료 후 워크트리가 남는지, `SessionStart` 훅이 워크트리 안에서 도는지
2. 서버 두 개 동시 기동: 앱 표시, 합산 메모리
3. "Enable Remote Control?" 수락이 계정 단위인지 폴더 단위인지. v5 설치 때 2.1.278은 이 질문 없이 바로 연결됐다
4. managed `ask`가 폰 프롬프트를 띄우고 "다시 묻지 않기" 뒤에도 유지되는지
5. `pocket trust` 절차가 다른 리포에서도 그대로 되는지
6. **`claude agents --json`이 Remote Control 세션을 보여주는지**, 대화 중·빌드 중·승인 대기일 때 `status`/`waitingFor`가 어떻게 나오는지 (§5-8의 새 유휴 판정이 여기 달려 있다)

---

## 부록 A. 확인한 사실

공식 문서(code.claude.com, AWS, systemd 소스, 텔레그램 Bot API) 조사와 박스 실측. "추정"은 문서가 직접 말하지 않는 부분.

- **F1 신뢰.** git 리포 루트 단위로 `~/.claude.json`의 `projects["<경로>"].hasTrustDialogAccepted`에 저장된다. 워크트리는 본 체크아웃의 신뢰를 따른다. 리포 밖에서는 시작 폴더와 그 하위가 신뢰되지만 안에 든 다른 리포는 제외. 홈 폴더 신뢰는 저장되지 않는다. 신뢰 전용 플래그·환경변수는 없다.
- **F2 미신뢰 폴더.** `claude remote-control`은 대화상자 없이 `Error: Workspace not trusted`로 종료한다.
- **F3 `--spawn worktree`.** 워크트리는 `<리포>/.claude/worktrees/<이름>/`, 브랜치 `worktree-<이름>`(추정). 푸시 안 한 커밋이 있으면 Claude Code가 삭제를 거부한다(`git worktree remove`에는 이 보호가 없음). `--capacity`(기본 32)에 포함. `.env` 복사·설치 같은 자동 준비는 없다. "Before editing files, Claude moves the session into an isolated git worktree" — 워크트리 이동이 편집 직전에 일어날 수 있다.
- **F4 managed settings.** Linux 경로 `/etc/claude-code/managed-settings.json`, 최우선. "an allow rule there doesn't outrank an ask rule from a project or managed file" — 사용자 `allow`와 "다시 묻지 않기"가 managed `ask`를 끄지 못한다. `defaultMode`도 managed에서 설정 가능.
- **F5 `auto` 전환.** `/doctor`가 사용자 설정에 `defaultMode: auto`를 제안하는 경로와, 권한 프롬프트의 "Yes, and switch to auto mode" 경로가 문서에 있다.
- **F6 여러 서버.** 폴더가 다른 서버 여러 개에 문서상 제한 없음. 같은 폴더 두 서버만 충돌. 앱에서의 표시는 문서에 없음.
- **F7 푸시.** 두 설정은 계정 단위, 작업 완료·입력 필요일 때만. 서버 기동 알림은 문서에 없음.
- **F8 SSM 문서.** `allowedValues`·`allowedPattern`은 SendCommand 시점에 AWS가 검증한다. `ssm:SendCommand`를 문서 ARN + 인스턴스 ARN으로 제한하는 AWS 공식 예제가 있다.
- **F9 AWS 프로필.** `role_arn` + `credential_source = Ec2InstanceMetadata`로 인스턴스 역할에서 다른 역할을 넘겨받는다.
- **F10 systemd.** 시스템 유닛의 `%h`는 `User=`를 무시하고 `/root`(업스트림 이슈 #30782). `EnvironmentFile=-…/%i.env`는 인스턴스별로 동작. `Slice=`로 묶은 인스턴스에 합산 메모리 한도 가능. 인스턴스 이름에 `.` 허용.
- **F11 텔레그램.** `callback_data` 1~64바이트. 버튼 누름마다 `answerCallbackQuery` 필요. 버튼만 바꿀 때는 `editMessageReplyMarkup`.
- **F12 유휴 CPU (실측 2026-09-22).** 대화 없는 서버 하나(서버 + 세션 프로세스)가 60초에 CPU 1초. 5분 간격 샘플이 매번 달라 idle-watch가 영원히 활동으로 판정.
- **F13 샌드박스.** Remote Control에 `--sandbox` 플래그가 있다. 기본값은 작업 폴더 밖 쓰기만 막고 **읽기는 자격증명 파일 포함 허용**. `sandbox.filesystem.denyRead`로 막으며 OS 수준이라 모든 하위 프로세스에 적용. Ubuntu는 `bubblewrap`·`socat` 필요.
- **F15 세션 상태 조회.** `claude agents --json`은 살아 있는 세션을 JSON 배열로 출력하고 종료한다. 항목마다 `cwd`·`kind`·`startedAt`, 프로세스가 살아 있으면 `pid`·`status`, 대기 중이면 `waitingFor`(`permission prompt`, `input needed`, `sandbox request`, `worker request`, `dialog open`), 백그라운드 세션이면 `state`(`working`/`blocked`/`done`/`failed`/`stopped`). `--cwd <경로>`로 특정 폴더 하위만 볼 수 있다. Remote Control 서버가 띄운 세션이 여기 포함되는지는 실측 필요(§8-6).
- **F14 메인 세션.** `--create-session-in-dir`(기본 켜짐)은 기동 시 현재 폴더에 세션 하나를 미리 만들고, worktree 모드에서도 이 세션은 현재 폴더에 남는다. 끄면 서버 정지 시 세션이 보관 처리되어 복귀할 게 없다.

## 부록 B. 리뷰 반영 기록

초안을 독립 리뷰어가 검토해 15건을 지적했고 모두 반영했다. 주요 변경:
- 메인 세션(F14)과 capacity 계산을 명시하고 결정 2번으로 올림
- `prune`이 미푸시 커밋을 직접 검사하도록 수정 (`git worktree remove`는 보호하지 않음)
- 관리자 키 항목을 감사 결과(유출 증거 없음)와 함께 "박스 파일 삭제 권장, 교체는 선택"으로 정정. 맥과 같은 키임을 확인
- 프로젝트별 AWS 역할은 보안 경계가 아님을 명시하고 선택 사항으로 낮춤
- 워크트리 정리·디스크 표시를 단계 3에서 단계 1로
- 리포 이동 대신 링크 + `realpath` (신뢰·기록 보존)
- `pocket trust`의 경쟁 조건 대책(재확인·재시도)과 키 입력 순서 명시
- "Enable Remote Control?" 확인 항목, `SessionStart` 훅 타이밍과 대안, JSON 경계·버튼 응답 순서, `/add`의 소유자 제한
- 기각안(`~/work` 서버 하나) 추가, `--spawn session` 제거, 메모리 한도 방식, IAM 템플릿 처리, 폰 가독성

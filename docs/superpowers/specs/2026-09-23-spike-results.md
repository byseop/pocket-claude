# 단계 1 실측 결과 (2026-09-23)

박스에서 사용자와 함께 진행. 설계안 §8의 확인 항목에 대한 답이다. 스펙 부록 A의 F3·F6·F15는 이 결과로 확정한다.

## 방법

1. `claude-rc@ops`를 멈추고 같은 리포에서 손으로 서버를 띄웠다:
   `claude remote-control --name spike --spawn worktree --capacity 3 --permission-mode default --no-chrome`
2. 사용자가 폰 앱에서 그 환경에 **새 세션**을 만들고 "spike-test.txt에 hello를 쓰고 커밋" 시켰다 (푸시는 하지 않음)
3. 박스에서 결과를 관찰하고, 단계 1에서 만든 `pocket` CLI를 실제 리포에 돌려 봤다

## 결과

### S1. 워크트리 경로와 브랜치 이름

```
worktree /home/ubuntu/<repo>/.claude/worktrees/bridge-cse_<세션ID>
branch   refs/heads/worktree-bridge-cse_<세션ID>
locked   claude agent bridge-cse_<세션ID> (pid ... start ...)
```

- 위치는 스펙 F3대로 `<리포>/.claude/worktrees/` 아래다.
- 이름은 사람이 정한 이름이 아니라 **세션 ID**(`bridge-cse_...`)다. 브랜치도 같은 이름에 `worktree-` 접두사가 붙는다.
- **워크트리는 잠긴 상태로 만들어진다.** `git worktree remove`는 잠긴 워크트리를 거부한다. 정리 기능은 잠금을 인식해야 한다.
- 크기는 4.1MB였다(의존성 미설치). `yarn install`을 하면 프로젝트당 0.8GB가 더 붙는다.

### S2. 세션 준비 (`.env`)

워크트리에 `.env`가 **없다**(확인). 본 체크아웃에는 있다. 설계의 `SessionStart` 훅(Task 6)이 필요하다는 뜻이다. 실행 시점(본 체크아웃에서 뜨는지 워크트리에서 뜨는지)은 훅을 넣은 뒤에 확인한다.

### S3. 미푸시 판정

```
$ git -C <워크트리> log --oneline '@{u}..HEAD' ; echo $?
128                                   ← 업스트림 없음
$ git -C <워크트리> log --oneline origin/HEAD..HEAD
7848bd1 [test] spike-test.txt 추가     ← 미푸시 커밋을 잡아냄
```

앱이 만든 워크트리에는 업스트림이 없다. 즉 첫 번째 형태는 **항상 실패**한다. 실패를 "푸시할 것 없음"으로 읽던 최초 구현은 실제로 커밋을 지울 수 있었다. 수정 라운드 2의 안전 판정(rc를 확인하고, 둘 다 실패하면 보존)이 실전에서 필요했다.

### S4. 병합 브랜치 표시

```
$ git branch --merged
+ feat/korean-titles-1000
* main
```

다른 워크트리에 체크아웃된 브랜치는 `+`로 표시된다. `*`만 벗기던 코드는 아무것도 매칭하지 못했다. 수정 라운드 2의 `lstrip('*+ ')`가 맞다.

### S5. 세션 상태 조회 (유휴 판정의 근거)

```
$ claude agents --json
[
  {"pid":..., "cwd":"/home/ubuntu/<repo>", "kind":"interactive", "status":"idle", "name":"..."},
  {"pid":..., "cwd":"/home/ubuntu/<repo>/.claude/worktrees/bridge-cse_...", "kind":"interactive", "status":"idle", "name":"..."}
]
```

Remote Control 서버가 띄운 세션과 워크트리 세션이 **모두 나온다**. `cwd`로 어느 프로젝트인지도 구분된다. 유휴 자동 정지를 되살릴 때 이 출력을 주 신호로 쓸 수 있다(지금은 수동 정지 결정).

### S6. 용량 계산

기동 직후 `Capacity: 1/3`, 앱에서 세션 하나를 만든 뒤 `2/3`. 설계의 `--capacity = 1 + 작업 세션 수`가 맞다. 첫 세션은 본 체크아웃에 남고 워크트리를 쓰지 않는다.

### S7. 프로세스 식별

```
pid=1720 comm=claude    exe=.../versions/2.1.278   ← 서버
pid=2018 comm=2.1.278   exe=.../versions/2.1.278   ← 세션 (cwd = 워크트리)
```

세션 프로세스의 `comm`은 `claude`가 아니라 **버전 문자열**이다. `comm == 'claude'`로 찾던 "사용 중" 판정은 항상 거짓이었다. 실행 파일 경로로 판정해야 한다.

### S8. 서버 이름과 4시간 복귀

`--name spike`로 띄웠는데도 화면의 세션 이름은 이전 세션의 이름이 그대로 남았다. 같은 디렉터리에서 4시간 이내에 재기동하면 이전 세션이 복귀하기 때문이다(스펙 F5). 세션 이름은 서버 옵션보다 세션 자체의 기록을 따른다.

## CLI를 실제 리포에 돌린 결과 — 설계 결함 발견

```
$ pocket prune <프로젝트> --dry-run
would_remove: ["/home/ubuntu/workspace/<사용자가 직접 만든 워크트리>"]
kept:         [{bridge-cse_..., "미푸시 커밋이 있어요"}]
```

**사용자가 손으로 만든 워크트리가 삭제 후보에 들어갔다.** `git worktree list`는 리포의 모든 워크트리를 돌려주는데, 정리 대상은 앱이 만든 `<리포>/.claude/worktrees/` 아래로 한정해야 한다. 또 `refs/heads/feat/korean-titles-1000`이 `korean-titles-1000`으로 잘려 나왔다(브랜치 이름에 `/`가 있을 때).

## 반영 (Task 4 수정 라운드 3)

1. `trees`·`prune`의 범위를 `<리포>/.claude/worktrees/` 아래로 제한
2. 브랜치 이름은 `refs/heads/` 접두사만 제거
3. 잠긴 워크트리는 보존(사유: 세션이 잠갔어요)
4. 프로세스 판정을 `comm` 대신 실행 파일 경로로

## 스펙에 남는 영향

- F3의 "브랜치 `worktree-<이름>`"은 "`worktree-<세션ID>`"로 정정한다. 워크트리 이름을 사람이 고를 수 없다.
- 워크트리 잠금은 스펙에 없던 사실이다(F3 보강).
- F15(세션 상태 조회)는 실측으로 확인됐다.
- **미확인으로 남는 것.** 이 실측은 서버 하나·리포 하나로만 했다. "Enable Remote
  Control?" 수락(§8-3)이 계정 단위인지 프로젝트(폴더) 단위인지는 여전히 열린
  질문이다 — 첫 실제 2-프로젝트 이행(`docs/SETUP.md` 11단계, 두 번째 프로젝트를
  실제로 켜 보는 시점)에서 확인하고 이 문서와 스펙·SETUP·OPERATIONS를 갱신한다.

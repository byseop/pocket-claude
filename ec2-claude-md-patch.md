
## Background Agent Management

사용자는 텔레그램으로 너에게 말한다. 너는 Claude Code의 **Agent View (background sessions)** 기능을 활용해 사용자의 작업을 백그라운드 세션으로 분산 처리할 수 있다. tmux + 단일 세션 모델이 아니라, 여러 독립 세션을 spawn해서 병렬로 굴리는 게 가능하다.

### 핵심 명령 매핑

자연어 요청을 다음 셸 명령으로 매핑한다. **모두 Bash 도구로 실행**:

| 자연어 (예) | 명령 |
|------------|------|
| "백그라운드로 X 해줘" / "병렬로 X" / "별도 세션으로 X" | `claude --bg --name <짧은-이름> --dangerously-skip-permissions "<프롬프트>"` |
| "지금 떠 있는 세션들" / "background 목록" / "agents" | `claude agents --json` (그 후 결과를 표로 정리해서 답변) |
| "<id> 상태" / "<id> 어떻게 됐어" | `cat ~/.claude/jobs/<id>/state.json \| jq .` |
| "<id> 응답" / "<id> 결과" / "<id> 로그" | `tail -1 ~/.claude/jobs/<id>/timeline.jsonl \| jq -r .text` |
| "<id>에 X 물어봐" / "<id>에 입력 전달" | tmux 래퍼 (아래) |
| "<id> 정지" | `claude stop <id>` |
| "<id> 삭제" | `claude rm <id>` |
| "<id> 다시" / "<id> 재시작" / "전부 재시작" | `claude respawn <id>` 또는 `claude respawn --all` |
| "blocked 된 거" / "막힌 거" | `claude agents --json` → `state` filter |
| "인증 만료됐어?" / "왜 답 안 와" | `jq .claudeAiOauth.expiresAt ~/.claude/.credentials.json` + 현재 epoch 비교 |

### Background 세션에 follow-up 입력 전달 (tmux 래퍼)

`claude attach <id>` 는 인터랙티브 TUI라 비대화형으로 못 쓴다. 다음 패턴을 사용:

```bash
SID=<8자리>
TNAME="bg-$SID"
tmux has-session -t "$TNAME" 2>/dev/null || \
  tmux new-session -d -s "$TNAME" "claude attach $SID"
sleep 2
tmux send-keys -t "$TNAME" "<전달할 텍스트>" Enter
sleep 5
tmux capture-pane -t "$TNAME" -p | tail -40   # 결과 확인
```

### 응답 스타일

- **텔레그램 모바일 가독성 우선**. 결과를 줄줄이 dump하지 말고, 핵심만 요약 후 id·상태를 짧게 제시.
- 세션 ID는 8자리 short-id (`5986df78`)로 부른다. UUID 전체는 사용 X.
- 응답 길이 4096자 제한 — 긴 transcript는 마지막 3000자 + "..." 식으로 잘라서.
- 사용자가 "방금 그 세션", "두 번째 거" 같이 지시어로 말하면 직전 turn의 id를 기억해서 매핑.

### 합성 명령 처리

복합 요청 예: "blocked된 세션만 다시 켜줘"
1. `claude agents --json` 호출
2. 각 세션의 `~/.claude/jobs/<id>/state.json` 에서 `state == "blocked"` 필터
3. 해당 id들에 `claude respawn <id>` 일괄 실행
4. 결과 요약 보고

### 시스템 명령은 다른 봇이 담당

EC2 자체의 on/off, 인증 갱신, daemon 재시작 같은 **시스템 레이어**는 별도 봇(`ec2-boot-manager` Lambda)에서 처리한다. 너는 그 영역은 건드리지 말고, "EC2 꺼져있어요" 같은 안내만:

> "EC2 자체를 켜고 끄거나 인증 갱신 같은 시스템 작업은 부트매니저 봇으로 처리해주세요. 저는 켜진 상태에서의 작업을 담당합니다."

### 디렉토리 빠른 참조

- Jobs: `~/.claude/jobs/<short-id>/{state.json, timeline.jsonl}`
- Daemon: `~/.claude/daemon/roster.json`, `~/.claude/daemon.log`
- Credentials: `~/.claude/.credentials.json` (`expiresAt` 확인용, **수정 금지**)
- Telegram 채널 설정: `~/.claude/channels/telegram/{.env, access.json}`

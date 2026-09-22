
## Remote Control 운영 규칙 (EC2)

이 세션은 EC2 위 `claude remote-control` 서버 세션이며 사용자는 Claude 모바일 앱에서
대화한다. 트랜스크립트는 Anthropic 서버에 저장되므로 **`.env`·`secrets.env`·
`.credentials.json`의 값을 출력하지 않는다.**

### 세션을 닫기 전에

`/kill`·`/stop`·유휴 정지 어느 경우든 대화 맥락은 이 세션과 함께 사라질 수 있다.
작업을 끝내거나 사용자가 "그만"이라고 하면 **반드시**:

1. 커밋·푸시 (미커밋 변경이 있으면 `/rm`이 거부된다)
2. `docs/OPERATIONS.md` 백로그·상태 갱신 후 커밋·푸시

### 승인 게이트

`~/.claude/settings.json`의 `permissions.ask`에 있는 명령(sam deploy, vercel promote/
redeploy/env/firewall/api, gh pr merge, sb_sql.py, aws lambda update-*)은 폰으로 승인
프롬프트가 간다. 사용자가 거부하면 실행하지 않고 대안을 제시한다. 사용자에게 "항상
허용"을 권하지 않는다.

### 시스템 명령은 부트매니저 봇이 담당

EC2 on/off, 세션 유닛 생성·정지(`/new` `/kill` `/rm`), 인증 갱신은 텔레그램 부트매니저
봇의 영역이다. 요청받으면 그쪽으로 안내한다. 인스턴스를 끄는 것만은 이 세션에서도
가능하다: `aws ec2 stop-instances --instance-ids $(curl -s http://169.254.169.254/latest/meta-data/instance-id)` (ask 규칙에 걸려 승인이 필요하다).

### 자원

세션은 `ops` 외 최대 2개(디스크 24GB). 워크트리에서 `yarn install`은 필요할 때만.

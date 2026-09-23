# pocket-claude

EC2 위 Claude Code Remote Control 세션을 텔레그램 봇으로 켜고 끄는 개인 프로젝트. **퍼블릭 저장소**다.

## 민감정보 — 절대 커밋 금지

이 저장소는 공개돼 있다. 아래 값은 코드·문서·주석·커밋 메시지·조사 보고서 어디에도 넣지 않는다.

- AWS 계정 ID (12자리), EC2 인스턴스 ID (`i-…`), 보안 그룹 ID, API Gateway ID, 인스턴스 프라이빗 IP·호스트명
- 텔레그램 봇 토큰, chat ID
- Anthropic OAuth 토큰·API 키 (`sk-ant-…`), GitHub PAT, Vercel·Supabase 토큰, AWS 액세스 키
- `.env`, `.credentials.json`, `secrets.env`, `telegram.env`, `samconfig.toml`의 내용

실제 값은 로컬 `.env`(gitignore), Lambda 환경변수, EC2의 `~/.config/pocket-claude/*.env`에만 둔다.
문서에서 값을 가리켜야 하면 플레이스홀더를 쓴다: `AWS_ACCOUNT_ID`, `INSTANCE_ID`, `<TOKEN>`.

**커밋 전에 반드시 스캔한다.** 특히 다른 세션이 만든 조사 보고서·설계 문서처럼 내가 쓰지 않은 파일은 예외 없이:

```bash
grep -rnE 'i-[0-9a-f]{17}|[0-9]{12}|[0-9]{8,10}:AA[A-Za-z0-9_-]{33}|sk-ant-|github_pat_|ghp_|sbp_|AKIA|172\.31\.' \
  --exclude-dir=.git .
```

히트가 있으면 플레이스홀더로 바꾼 뒤 커밋한다. 예시 파일(`.env.example`, `ec2/*.env.example`)의 더미 값은 허용한다.

이미 푸시된 뒤 발견하면 force-push만으로는 부족하다(GitHub의 PR 참조가 옛 커밋을 붙잡는다). 2026-09-22에 조사 보고서의 계정·인스턴스 ID 때문에 저장소를 삭제·재생성한 전례가 있다.

## 구조

- `src/lambda_function.py` — 텔레그램 웹훅 Lambda (v6). 전원 스위치 + 박스의 `pocket` CLI를 SSM으로 호출하는 얇은 캐스터. Claude 기동에는 관여하지 않는다
- `ec2/pocket` — 프로젝트 목록·기동·정지·신뢰·워크트리 정리를 다루는 박스 쪽 CLI. 로직은 여기 모인다
- `ec2/` — 그 외 systemd 템플릿 유닛·slice, tmux 래퍼, sudoers, 유휴 감시(현재 미사용), 설치·이행 스크립트, settings·env 템플릿
- `tests/` — Lambda·`pocket`은 stdlib `unittest`, 셸은 bash 단정문 (macOS·Ubuntu 양쪽에서 돌아야 함)
- `docs/SETUP.md` 1회 설정, `docs/OPERATIONS.md` 운영 런북, `docs/superpowers/` 설계·계획

## 규칙

- Lambda는 블로킹하지 않는다 (API Gateway 30초). SSM 스크립트는 `/bin/sh`(dash) 문법만
- 셸·코드 주석은 영어, 문서·텔레그램 응답은 한글. 커밋 메시지는 한글 `[태그] 설명`, Claude 서명 트레일러 없음
- 테스트: `python3 -m unittest discover -s tests && bash tests/test_claude_rc_wrap.sh && bash tests/test_worktree_env_hook.sh && bash tests/test_idle_watch.sh`

# 최초 설정 (1회) — Remote Control 모드

EC2에 SSM 세션 셸로 들어가 `sudo -u ubuntu -i`로 수행한다. 브라우저 인증이 필요한
단계(1·2·3)는 봇이 대신할 수 없다. 순서대로 한다.

## 0. 용량 확인

```bash
df -h / ; free -m ; curl -s http://169.254.169.254/latest/meta-data/instance-type
```

24GB EBS면 워크트리(세션)는 `ops` 외 2개까지다. 세션 3개 이상을 쓰려면 t3.large +
EBS 40GB로 늘린다. 세션당 메모리는 유휴 121MB, 포화 518MB(맥 실측).

## 1. 인증 — claude.ai 로그인으로 교체

`claude setup-token`/`CLAUDE_CODE_OAUTH_TOKEN`으로는 Remote Control 세션을 만들 수
없다. `.env`에 토큰이 남아 있으면 "API-key auth takes precedence"로 거부된다.

```bash
grep -v CLAUDE_CODE_OAUTH_TOKEN ~/.claude/.env > /tmp/e && mv /tmp/e ~/.claude/.env
env | grep -E 'ANTHROPIC_(API_KEY|AUTH_TOKEN|BASE_URL)|DISABLE_TELEMETRY|DO_NOT_TRACK|DISABLE_GROWTHBOOK|CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'
#  ↑ 하나라도 잡히면 제거 (Remote Control 자격 조건). ~/.claude/settings.json 의 env 블록도 확인
claude auth login      # claude.ai 선택
claude doctor          # "Remote Control" 항목 확인. 키 입력으로 종료
```

## 2. 도구 로그인

```bash
gh auth login                                   # 또는 secrets.env 의 GH_TOKEN
npx vercel login
cd ~/gamer4info && npx vercel link --scope byseops-projects   # 또는 VERCEL_TOKEN
```

## 3. secrets.env · telegram.env

```bash
umask 077; mkdir -p ~/.config/gamer4
cp /tmp/secrets.env.example ~/.config/gamer4/secrets.env     # 리포 ec2/secrets.env.example
cp /tmp/telegram.env.example ~/.config/gamer4/telegram.env   # 리포 ec2/telegram.env.example
vi ~/.config/gamer4/secrets.env      # GH_TOKEN, VERCEL_TOKEN, SUPABASE_ACCESS_TOKEN(맥 키체인 값)
vi ~/.config/gamer4/telegram.env     # TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (부트매니저 봇)
chmod 600 ~/.config/gamer4/secrets.env ~/.config/gamer4/telegram.env
```

`secrets.env`는 `claude-rc@.service`가 세션 환경변수로 올리므로 거기 넣은 값은 세션이
`env`로 볼 수 있다. 부트매니저 봇 토큰을 `telegram.env`로 나눈 이유다 — 이 파일은
`idle-watch.sh`만 읽는다.

## 4. 리포 비밀 파일

`~/gamer4info/.env`(REVALIDATE_SECRET 포함)와 `~/gamer4info/infra/samconfig.toml`을
붙여넣는다. 둘 다 git에 없다.

## 5. Claude Code 설정

```bash
cp /tmp/claude-settings.json ~/.claude/settings.json     # 리포 ec2/claude-settings.json
cat /tmp/ec2-claude-md-patch.md >> ~/.claude/CLAUDE.md   # 리포 ec2-claude-md-patch.md
```

user settings에 넣어야 `defaultMode`가 먹는다. 프로젝트 `.claude/settings.json`의
`auto`/`bypassPermissions`는 무시된다.

## 6. 인스턴스 역할 IAM 정책 (맥에서)

`iam/gamer4-operator-policy.json`의 `AWS_ACCOUNT_ID`를 채운 뒤:

```bash
aws iam put-role-policy --role-name ec2-ssm-role \
  --policy-name gamer4-operator --policy-document file://iam/gamer4-operator-policy.json
```

AWS 액세스 키는 EC2에 복사하지 않는다. `iam:PassRole`이 없으므로 스택의 IAM 역할을
바꾸는 `sam deploy`는 EC2에서 실패한다 — 그런 변경은 맥에서 한다.

보안 그룹에서 SSH 22 인바운드를 제거한다 (SSM만 사용).

## 7. EC2 파일 설치 (root)

리포 `ec2/*`를 `/tmp`로 복사한 뒤:

```bash
sudo bash /tmp/install.sh
```

`claude-rc@ops`를 enable만 하고 start하지 않는다. 첫 기동은 다음 단계에서 대화형으로 한다.

## 8. Remote Control 1회 수락 (대화형, ubuntu)

```bash
cd ~/gamer4info
claude                      # 워크스페이스 신뢰 수락 → /exit
claude remote-control --name gamer4-ops --spawn same-dir --capacity 2 --permission-mode default --no-chrome
#   "Enable Remote Control? (y/n)" → y
#   QR을 폰 Claude 앱으로 스캔 → 대화 확인 → Ctrl+C
```

폰 앱 `/config`에서 **Push when Claude decides**, **Push when actions required**를 켠다.

## 9. 상시화

```bash
sudo systemctl enable --now claude-rc@ops
systemctl status claude-rc@ops
```

텔레그램에서 `/status` → `🟢 claude-rc@ops active`, `✅ 인증 OK`.

## 10. 스모크 테스트

| 확인 | 기대 |
|---|---|
| `/stop` → `/start` | 90초 내 앱에 `gamer4-ops` 온라인 |
| `/new t1` → `/sessions` | `gamer4-t1` 🟢, 앱에서 대화 가능 |
| `/kill t1` | 앱에서 오프라인, `/sessions`에 ⚪ |
| `/rm t1` | 워크트리 삭제 (변경 있으면 거부) |
| 폰에서 "sam deploy 해줘" | 폰에 승인 프롬프트, 거부하면 실행 안 됨. **"항상 허용" 누르지 않는다** |
| 폰에서 "sam deploy 해줘" 승인 | CreateChangeSet 성공 (정책 부족이면 여기서 AccessDenied) |
| `IDLE_MINUTES=5 ~/idle-watch.sh` 를 6분 간격 2회 | 두 번째에 텔레그램 알림 후 정지 |

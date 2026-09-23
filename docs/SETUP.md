# 최초 설정 (1회) — Remote Control 모드

EC2에 SSM 세션 셸로 들어가 `sudo -u ubuntu -i`로 수행한다. 브라우저 인증이 필요한
단계(1·2·3)는 봇이 대신할 수 없다. 아래는 프로젝트 이름을 `myapp`으로 든 예시다.
순서대로 한다.

## 0. 용량 확인

```bash
df -h / ; free -m ; curl -s http://169.254.169.254/latest/meta-data/instance-type
```

동시 세션 수는 프로젝트별 `POCKET_SESSIONS`(기본 2)에 본 체크아웃 세션 하나를 더한
값(`1 + POCKET_SESSIONS`)이고, `pocket up`은 디스크 여유가 `POCKET_MIN_FREE_GB`(기본
3GB) 미만이면 거부한다. 24GB EBS면 프로젝트 하나가 넉넉하다. 세션 3개 이상을 동시에
쓰려면 t3.large + EBS 40GB로 늘린다. 세션당 메모리는 유휴 약 121MB, 포화 약 518MB(맥
실측).

## 0-1. 파일 준비

아래 단계는 리포의 `ec2/*`와 루트 `ec2-claude-md-patch.md`가 인스턴스 `/tmp`에 있다고
가정한다. 맥에서 먼저 올린다.

```bash
# commands 배열에 파일별 `cat > /tmp/<파일> <<'EOF' ... EOF` 를 담은 JSON 파라미터 파일을
# 만들어 보낸다. 한글·따옴표가 섞이면 --parameters 인라인 파싱이 깨지므로 항상 파일로 준다.
aws ssm send-command --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript --parameters file:///tmp/put-files.json
```

SSM 세션 셸에서 `cat > /tmp/<파일>` 로 직접 붙여넣어도 된다.

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
npx vercel login                                # 또는 secrets.env 의 VERCEL_TOKEN
cd ~/work/myapp && npx vercel link              # 프로젝트별로 필요할 때
```

## 3. secrets.env · telegram.env

```bash
umask 077; mkdir -p ~/.config/pocket-claude
cp /tmp/secrets.env.example ~/.config/pocket-claude/secrets.env     # 리포 ec2/secrets.env.example
cp /tmp/telegram.env.example ~/.config/pocket-claude/telegram.env   # 리포 ec2/telegram.env.example
vi ~/.config/pocket-claude/secrets.env      # GH_TOKEN, VERCEL_TOKEN 등(맥 키체인 값)
vi ~/.config/pocket-claude/telegram.env     # 현재 미사용(수동 정지 결정). 되살릴 때를 위해 둔다
chmod 600 ~/.config/pocket-claude/secrets.env ~/.config/pocket-claude/telegram.env
```

`secrets.env`는 `claude-rc@.service`가 모든 프로젝트의 세션 환경변수로 올리므로 거기
넣은 값은 세션이 `env`로 볼 수 있다. 프로젝트마다 다른 값(동시 세션 수, `AWS_PROFILE`
등)이 필요하면 `~/.config/pocket-claude/projects/<이름>.env`에 따로 둔다 — 유닛의
`EnvironmentFile=-…/projects/%i.env`가 그 프로젝트에만 적용한다.

## 4. 프로젝트 비밀 파일

각 프로젝트의 `.env`나 `infra/samconfig.toml` 같은 git에 없는 설정 파일을 그 리포에
직접 붙여넣는다(예: `~/work/myapp/.env`). 앱이 만드는 워크트리 세션에는 `SessionStart`
훅(`ec2/worktree-env-hook.sh`)이 본 체크아웃의 git 무시 파일(`.env*`, `.vercel/`)을
자동으로 복사하므로, 이 단계는 본 체크아웃에만 하면 된다.

## 5. Claude Code 설정

```bash
cp /tmp/claude-settings.json ~/.claude/settings.json     # 리포 ec2/claude-settings.json
cat /tmp/ec2-claude-md-patch.md >> ~/.claude/CLAUDE.md   # 리포 ec2-claude-md-patch.md
```

user settings에 넣어야 `defaultMode`가 먹는다. 프로젝트 `.claude/settings.json`의
`auto`/`bypassPermissions`는 무시된다. `SessionStart` 훅 경로(`/home/ubuntu/bin/
worktree-env-hook.sh`)는 7단계의 `install.sh`가 설치한다.

## 6. 인스턴스 역할 IAM 정책 (맥에서)

`iam/operator-policy.example.json`을 복사해 `AWS_ACCOUNT_ID`·`STACK_NAME`(배포용 스택
이름)·`FUNCTION_PREFIX`(그 스택 함수들의 공통 접두사)를 채운다. `FUNCTION_PREFIX-*`에
안 걸리는 함수(이 Lambda 자체를 셀프 업데이트하거나 별도 알림 함수 등)가 있으면
`LambdaExtraFunctions` 문의 `EXTRA_FUNCTION_NAME`을 실제 함수 이름으로 바꾼다. 필요
없으면 그 문 전체를 지운다. 채운 파일은 저장소 밖(`/tmp` 등)에 둔다.

```bash
cp iam/operator-policy.example.json /tmp/operator-policy.json   # 채운 뒤
aws iam put-role-policy --role-name ec2-ssm-role \
  --policy-name pocket-claude-operator --policy-document file:///tmp/operator-policy.json
```

AWS 액세스 키는 EC2에 복사하지 않는다. `iam:PassRole`이 없으므로 스택의 IAM 역할을
바꾸는 `sam deploy`는 EC2에서 실패한다 — 그런 변경은 맥에서 한다.

보안 그룹에서 SSH 22 인바운드를 제거한다 (SSM만 사용).

## 7. EC2 파일 설치 (root)

리포 `ec2/*`를 `/tmp`로 복사한 뒤:

```bash
sudo bash /tmp/install.sh
```

`pocket`·래퍼·`worktree-env-hook.sh`를 `~/bin`에 설치하고, systemd 유닛·slice·
sudoers를 등록하고, cron의 `idle-watch.sh` 등록을 지운다(있었다면). 프로젝트별 유닛은
여기서 enable하지 않는다 — 다음 두 단계에서 프로젝트마다 한다.

## 8. 프로젝트 등록

```bash
ln -s ~/myapp ~/work/myapp          # 리포는 옮기지 않는다. 실제 경로 어디든 링크만
pocket trust myapp                  # 대화형(내부에서 tmux로 신뢰 대화상자를 수락한다)
```

`pocket trust`가 실패하면(경쟁 상태 등) SSM 셸에서 직접 한다: `cd ~/work/myapp &&
claude` → 신뢰 수락 → `/exit`.

## 9. Remote Control 최초 1회 수락 (대화형, ubuntu)

계정당 한 번만 필요하다. 이미 다른 프로젝트에서 이 박스의 Remote Control을 수락한
적이 있으면 이 단계를 건너뛰고 10번으로 간다.

```bash
cd ~/work/myapp
claude remote-control --name myapp --spawn worktree --capacity 3 --permission-mode default --no-chrome
#   "Enable Remote Control? (y/n)" → y
#   QR을 폰 Claude 앱으로 스캔 → 대화 확인 → Ctrl+C
```

폰 앱 `/config`에서 **Push when Claude decides**, **Push when actions required**를 켠다.

## 10. 상시화

```bash
pocket up myapp
```

텔레그램에서 `/status` → `🟢 myapp active`, `✅ 인증 OK`. `pocket up`은 enable + start를
같이 하므로 재부팅해도 자동으로 돌아온다.

## 11. 프로젝트 추가

```bash
ln -s ~/workspace/other ~/work/other
pocket trust other
pocket up other
```

9번의 QR·"Enable Remote Control?" 수락은 계정 단위라 프로젝트를 더할 때마다 반복하지
않는다.

## 12. 스모크 테스트

| 확인 | 기대 |
|---|---|
| `/stop` → `/start` | 90초 내 앱에 켜 둔 프로젝트들이 다시 온라인(스티키) |
| `/projects` 버튼으로 켜고 끄기 | 🟢/⚪ 토글, 버튼이 그 자리에서 갱신 |
| 앱에서 새 세션 시작 | 워크트리 생성, 본 체크아웃의 `.env`가 자동 복사됐는지 확인 |
| `/trees myapp` | 워크트리 목록 + 미커밋/미푸시/잠김 표시 |
| SSM 셸에서 `pocket prune myapp --dry-run` → `pocket prune myapp` | 정리 대상 미리 보고 지움. 미푸시·잠긴 워크트리는 남는지 확인 |
| 재부팅 | `/up`으로 켜 둔 프로젝트만 자동 복귀, 나머지는 꺼진 채 |
| 폰에서 "sam deploy 해줘" | 승인 프롬프트가 뜬다, 거부하면 실행 안 됨. **"항상 허용" 누르지 않는다** |
| 폰에서 "sam deploy 해줘" 승인 | CreateChangeSet 성공 (정책 부족이면 여기서 AccessDenied) |
| 폰에서 ".env 읽어줘" 요청 | deny로 거부된다 |
| `/status` 를 정상 상태에서 3회 | "❌ 인증 실패" 오탐 없음 |

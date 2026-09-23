# pocket-claude v6 단계 1 — 범용화 코어 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 한 리포 전용이던 pocket-claude를 "박스의 `~/work/<이름>`에 있는 어떤 리포든 폰에서 켜고 끄는" 구조로 바꾼다. 프로젝트 추가에 코드 수정·재배포가 없어야 한다.

**Architecture:** 박스에 `pocket` CLI(Python, 표준 라이브러리)를 두고 프로젝트 조회·기동·정지·신뢰·워크트리 정리를 전부 여기서 한다. Lambda는 SSM으로 `pocket <동사> --json`을 부르고 결과를 텔레그램 문장과 인라인 버튼으로 바꾸는 얇은 층이 된다. 유닛 `claude-rc@<프로젝트>`는 `~/work/<프로젝트>`의 실제 경로에서 `claude remote-control --spawn worktree`를 띄우고, 작업 세션(워크트리)은 폰 앱이 만든다. 모든 유닛은 `claude-rc.slice`에 묶여 합산 메모리 한도를 받는다.

**Tech Stack:** Python 3.11+ (Lambda·CLI, stdlib `unittest`), bash (래퍼·훅·테스트), systemd 템플릿 유닛 + slice, tmux(유닛별 소켓), AWS SSM RunShellScript(`/bin/sh`), Telegram Bot API(인라인 키보드), Claude Code 2.1.278 Remote Control

**Spec:** `docs/superpowers/specs/2026-09-23-generalize-multi-project-design.md` (추천안 B 승인 2026-09-23). 사실 근거는 그 문서의 부록 A(F1~F15).

## Global Constraints

- 공개 저장소다. 코드·문서·주석·커밋 메시지에 계정 ID, 인스턴스 ID, 토큰, IP, 특정 프로젝트 이름을 넣지 않는다. 커밋 전 `CLAUDE.md`의 스캔 명령을 돌린다.
- 커밋 메시지는 한글 `[태그] 설명`. **트레일러(Co-Authored-By 등)를 넣지 않는다.**
- 셸·파이썬 주석은 영어. 텔레그램 응답·문서는 한글.
- Lambda의 어떤 경로도 길게 블로킹하지 않는다. `ssm_run` 폴링 상한 25초, 버튼 응답은 SSM 호출 **전에** `answerCallbackQuery`.
- **Lambda는 Claude를 직접 띄우지 않는다.** 유닛을 켜고 끌 뿐이고 프로세스 수명은 systemd가 갖는다.
- 프로젝트 이름 규칙 `^[a-z0-9][a-z0-9-]{0,23}$`. 검증은 Lambda와 `pocket` 두 곳에서 한다(단계 2에서 SSM 문서 패턴이 셋째 층이 된다).
- SSM에 보내는 스크립트는 POSIX sh(dash)만 쓴다. bash 전용 문법 금지.
- 테스트는 macOS(BSD 도구)와 Ubuntu 양쪽에서 돌아야 한다. `date -d`, `touch -d`, `find -printf`, GNU `stat` 전용 옵션 금지 — `stat -c %Y ... || stat -f %m ...` 형태를 쓴다.
- Python은 3.11에서 돌아야 한다(맥 로컬). 3.12 전용 문법 금지, 외부 패키지 금지.
- 경로는 절대경로로 쓴다. systemd 시스템 유닛의 `%h`는 `/root`다(F10).
- 기존 v5 동작은 이행(Task 9)이 끝나기 전까지 그대로 살아 있어야 한다.

## 선행 조건 — Task 1(실측)을 먼저 끝낸다

Task 2 이후는 Task 1의 결과에 의존한다. 특히 `--spawn worktree`의 실제 경로·브랜치 이름과 `claude agents --json`의 출력이 다르면 스펙을 먼저 고친다. Task 1은 사용자가 폰 앱을 조작해야 하므로 **사람이 함께 진행**하고, 결과를 `docs/superpowers/specs/2026-09-23-spike-results.md`에 적는다.

## File Structure

| 파일 | 상태 | 책임 |
|---|---|---|
| `ec2/pocket` | 신규 | 박스 CLI. 프로젝트 조회·기동·정지·신뢰·워크트리 정리. JSON 출력 |
| `tests/test_pocket.py` | 신규 | CLI 단위 테스트 (가짜 `systemctl`·`git`·`claude`, 임시 루트) |
| `ec2/claude-rc-wrap.sh` | 수정 | 경로 상수 제거, `~/work/<p>` realpath, 신뢰 확인, spawn/capacity 옵션화 |
| `ec2/claude-rc@.service` | 수정 | 인스턴스별 EnvironmentFile, `Slice=`, StartLimit 유지 |
| `ec2/claude-rc.slice` | 신규 | 합산 메모리 한도 |
| `ec2/claude-rc.sudoers` | 수정 | `enable|disable|restart|reset-failed` 추가 |
| `ec2/worktree-env-hook.sh` | 신규 | 워크트리 세션에 git 무시 파일 복사 (SessionStart) |
| `ec2/claude-settings.json` | 수정 | 훅 등록, gamer4 전용 규칙 제거 |
| `ec2/install.sh` | 수정 | 새 파일 배치, slice·cron 정리 |
| `ec2/migrate-v6.sh` | 신규 | v5 → v6 이행 (링크 생성, 유닛 전환, 설정 폴더 이동) |
| `src/lambda_function.py` | 수정 | v6. `pocket` 호출 + 버튼. `/new /kill /rm /sessions` 제거, `/projects /up /down /trees` 추가 |
| `tests/test_lambda_function.py` | 수정 | 새 명령·버튼·JSON 경계 |
| `README.md`, `docs/SETUP.md`, `docs/OPERATIONS.md`, `ec2-claude-md-patch.md` | 수정 | v6 구조. 특정 프로젝트 이름 제거 |
| `iam/gamer4-operator-policy.json` | 이름 변경 | `iam/operator-policy.example.json` (스택·함수 이름을 자리표시자로) |

`pocket`은 한 파일이지만 역할이 뚜렷한 층으로 나눈다: (1) 순수 헬퍼(이름 검증·경로 해석·JSON 포장), (2) 조회(프로젝트 목록·상태), (3) 변경(기동·정지·신뢰·정리). 테스트는 (1)(2)를 직접, (3)은 가짜 실행파일로 검증한다.

---

### Task 1: 실측 스파이크 (사람이 함께) + 유휴 cron 제거

**Files:**
- Create: `docs/superpowers/specs/2026-09-23-spike-results.md`
- 박스 상태 변경: cron에서 `idle-watch.sh` 등록 제거

**Interfaces:**
- Consumes: 없음
- Produces: 스파이크 결과 문서. Task 5(래퍼의 spawn 옵션), Task 4(`prune`의 워크트리 경로·브랜치 규칙), Task 6(훅 발화 시점)이 이 값을 쓴다

- [ ] **Step 1: 박스 기동과 cron 정리**

```bash
# 맥에서
aws ec2 start-instances --region ap-northeast-2 --instance-ids "$INSTANCE_ID"
# SSM으로 (ubuntu crontab에서 idle-watch 줄만 제거, 스크립트는 남긴다)
sudo -u ubuntu crontab -l | grep -v 'idle-watch.sh' | sudo -u ubuntu crontab -
sudo -u ubuntu crontab -l
```
기대: `idle-watch.sh` 줄이 사라지고 나머지 줄은 그대로.

- [ ] **Step 2: `claude agents --json` 출력 관찰 (F15)**

`claude-rc@ops`가 떠 있는 상태에서:
```bash
sudo -u ubuntu -H bash -lc 'cd /home/ubuntu/gamer4info && claude agents --json' | python3 -m json.tool | head -40
```
기록할 것: Remote Control 서버 세션이 목록에 나오는가, `kind`·`status` 값, 폰에서 대화를 보내는 동안 값이 어떻게 바뀌는가, 승인 프롬프트가 떠 있을 때 `waitingFor`가 나오는가.

- [ ] **Step 3: `--spawn worktree` 실동작 (앱 조작 필요)**

`ops` 유닛을 잠시 멈추고 손으로 서버를 띄운다:
```bash
sudo systemctl stop claude-rc@ops
sudo -u ubuntu -H bash -lc 'cd /home/ubuntu/gamer4info && claude remote-control --name spike --spawn worktree --capacity 3 --permission-mode default --no-chrome'
```
폰 앱에서 이 환경에 **새 세션**을 만들고 간단한 편집을 시킨 뒤 기록한다: 워크트리 경로, 브랜치 이름, `git worktree list` 결과, 세션을 앱에서 보관(archive)했을 때 워크트리가 남는지, 메인 세션(기동 시 자동 생성분)이 몇 번째로 세는지.

- [ ] **Step 4: 서버 두 개 동시 (F6)**

두 번째 리포로 손 서버를 하나 더 띄우고 앱에서 어떻게 보이는지, `free -m`으로 합산 메모리를 기록한다.

- [ ] **Step 5: 결과 기록과 게이트**

`docs/superpowers/specs/2026-09-23-spike-results.md`에 Step 2~4 결과를 적는다. 스펙 F3(경로 `<리포>/.claude/worktrees/<이름>`, 브랜치 `worktree-<이름>`)이나 F6과 다르면 **여기서 멈추고** 스펙을 고친 뒤 Task 2로 간다.

- [ ] **Step 6: 커밋**

```bash
git add docs/superpowers/specs/2026-09-23-spike-results.md
git commit -m "[docs] 단계 1 실측 결과 기록"
```

---

### Task 2: `pocket` CLI — 뼈대와 `list`

**Files:**
- Create: `ec2/pocket`
- Create: `tests/test_pocket.py`

**Interfaces:**
- Consumes: 박스 레이아웃 `~/work/<이름>`(실제 폴더 또는 링크), `~/.claude.json`, `~/.claude/projects/<슬러그>`
- Produces (이후 모든 Task가 쓴다):
  - 환경변수 오버라이드: `POCKET_ROOT`(기본 `/home/ubuntu/work`), `POCKET_HOME`(기본 `/home/ubuntu`), `POCKET_CLAUDE_JSON`, `POCKET_PROJECTS_DIR`, `POCKET_SUDO`(기본 `sudo -n`)
  - 출력 규약: 표준출력에 `===POCKET-BEGIN===` 줄, JSON 한 줄, `===POCKET-END===` 줄. 사람이 읽는 출력은 `--json` 없이 호출할 때만
  - JSON 봉투: `{"ok": true|false, "verb": "<동사>", "data": {...}, "error": "<메시지>"}`
  - 함수: `valid_name(name) -> bool`, `project_dir(name) -> str|None`(realpath, 없으면 None), `slug(path) -> str`(`/`·`.` → `-`), `list_projects() -> list[dict]`, `envelope(verb, data=None, error=None) -> dict`, `emit(obj)`
  - 프로젝트 레코드: `{"name", "path", "unit": "active|inactive|failed|unknown", "enabled": bool, "trusted": bool, "branch": str, "dirty": bool, "last_activity": int(epoch, 없으면 0), "worktrees": int}`

- [ ] **Step 1: 테스트 작성 (RED)**

`tests/test_pocket.py`:

```python
"""Unit tests for the pocket CLI. No AWS, no systemd: fake binaries on PATH."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

POCKET = str(Path(__file__).resolve().parents[1] / 'ec2' / 'pocket')

FAKE_SYSTEMCTL = """#!/bin/sh
# args: is-active|is-enabled <unit>   -> echo from $FAKE_STATE_DIR/<unit>.<verb>
verb=$1; unit=$2
f="$FAKE_STATE_DIR/$unit.$verb"
if [ -f "$f" ]; then cat "$f"; else echo unknown; fi
echo "$@" >> "$FAKE_STATE_DIR/systemctl.log"
"""

FAKE_GIT = """#!/bin/sh
echo "$@" >> "$FAKE_STATE_DIR/git.log"
case "$*" in
  *"rev-parse --abbrev-ref HEAD"*) echo "${FAKE_BRANCH:-main}" ;;
  *"status --porcelain"*) printf '%s' "${FAKE_DIRTY:-}" ;;
  *"worktree list --porcelain"*) printf '%s' "${FAKE_WORKTREES:-}" ;;
  *) : ;;
esac
"""


def write_exec(path, body):
    path.write_text(body)
    path.chmod(0o755)


class PocketCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.work = self.home / 'work'
        self.bin = self.root / 'bin'
        self.state = self.root / 'state'
        for d in (self.work, self.bin, self.state):
            d.mkdir(parents=True)
        write_exec(self.bin / 'systemctl', FAKE_SYSTEMCTL)
        write_exec(self.bin / 'git', FAKE_GIT)
        (self.home / '.claude.json').write_text(json.dumps({'projects': {}}))
        (self.home / '.claude' / 'projects').mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def make_project(self, name, trusted=False, unit='inactive', enabled=False):
        d = self.work / name
        (d / '.git').mkdir(parents=True)
        if trusted:
            p = self.home / '.claude.json'
            data = json.loads(p.read_text())
            data['projects'][str(d.resolve())] = {'hasTrustDialogAccepted': True}
            p.write_text(json.dumps(data))
        (self.state / f'claude-rc@{name}.service.is-active').write_text(unit + '\\n')
        (self.state / f'claude-rc@{name}.service.is-enabled').write_text(
            ('enabled' if enabled else 'disabled') + '\\n')
        return d

    def run_pocket(self, *args, **env):
        e = dict(os.environ)
        e.update({
            'PATH': f'{self.bin}:{e["PATH"]}',
            'POCKET_ROOT': str(self.work),
            'POCKET_HOME': str(self.home),
            'POCKET_CLAUDE_JSON': str(self.home / '.claude.json'),
            'POCKET_PROJECTS_DIR': str(self.home / '.claude' / 'projects'),
            'POCKET_SUDO': '',
            'FAKE_STATE_DIR': str(self.state),
        })
        e.update({k: str(v) for k, v in env.items()})
        return subprocess.run([sys.executable, POCKET, *args], capture_output=True,
                              text=True, env=e)

    def json_of(self, res):
        out = res.stdout
        self.assertIn('===POCKET-BEGIN===', out, res.stderr)
        body = out.split('===POCKET-BEGIN===')[1].split('===POCKET-END===')[0]
        return json.loads(body)


class TestPureHelpers(PocketCase):
    def test_valid_names(self):
        res = self.run_pocket('check-name', 'guam-go', '--json')
        self.assertTrue(self.json_of(res)['ok'])

    def test_invalid_names(self):
        for bad in ('Guam', 'a b', 'a;b', '../x', '', '-lead', 'a' * 25):
            res = self.run_pocket('check-name', bad, '--json')
            self.assertFalse(self.json_of(res)['ok'], bad)


class TestList(PocketCase):
    def test_empty_root(self):
        data = self.json_of(self.run_pocket('list', '--json'))
        self.assertEqual(data['data']['projects'], [])

    def test_lists_projects_sorted(self):
        self.make_project('beta')
        self.make_project('alpha')
        data = self.json_of(self.run_pocket('list', '--json'))
        self.assertEqual([p['name'] for p in data['data']['projects']], ['alpha', 'beta'])

    def test_follows_symlink_to_real_path(self):
        real = self.root / 'elsewhere' / 'myapp'
        (real / '.git').mkdir(parents=True)
        (self.work / 'myapp').symlink_to(real)
        (self.state / 'claude-rc@myapp.service.is-active').write_text('inactive\\n')
        (self.state / 'claude-rc@myapp.service.is-enabled').write_text('disabled\\n')
        p = self.json_of(self.run_pocket('list', '--json'))['data']['projects'][0]
        self.assertEqual(p['path'], str(real.resolve()))

    def test_skips_non_git_entries(self):
        (self.work / 'notrepo').mkdir()
        self.make_project('real')
        names = [p['name'] for p in self.json_of(self.run_pocket('list', '--json'))['data']['projects']]
        self.assertEqual(names, ['real'])

    def test_skips_invalid_names(self):
        d = self.work / 'Bad_Name'
        (d / '.git').mkdir(parents=True)
        self.assertEqual(self.json_of(self.run_pocket('list', '--json'))['data']['projects'], [])

    def test_reports_unit_and_enabled(self):
        self.make_project('app', unit='active', enabled=True)
        p = self.json_of(self.run_pocket('list', '--json'))['data']['projects'][0]
        self.assertEqual(p['unit'], 'active')
        self.assertTrue(p['enabled'])

    def test_reports_trust(self):
        self.make_project('trusted-one', trusted=True)
        self.make_project('untrusted')
        by = {p['name']: p for p in self.json_of(self.run_pocket('list', '--json'))['data']['projects']}
        self.assertTrue(by['trusted-one']['trusted'])
        self.assertFalse(by['untrusted']['trusted'])

    def test_reports_branch_and_dirty(self):
        self.make_project('app')
        data = self.json_of(self.run_pocket('list', '--json', FAKE_BRANCH='feat/x', FAKE_DIRTY=' M a.py\\n'))
        p = data['data']['projects'][0]
        self.assertEqual(p['branch'], 'feat/x')
        self.assertTrue(p['dirty'])

    def test_last_activity_from_transcripts(self):
        d = self.make_project('app')
        slug = str(d.resolve()).replace('/', '-').replace('.', '-')
        td = self.home / '.claude' / 'projects' / slug
        td.mkdir(parents=True)
        (td / 'a.jsonl').write_text('{}')
        p = self.json_of(self.run_pocket('list', '--json'))['data']['projects'][0]
        self.assertGreater(p['last_activity'], 0)

    def test_last_activity_zero_without_transcripts(self):
        self.make_project('app')
        p = self.json_of(self.run_pocket('list', '--json'))['data']['projects'][0]
        self.assertEqual(p['last_activity'], 0)

    def test_human_output_without_json_flag(self):
        self.make_project('app', unit='active')
        res = self.run_pocket('list')
        self.assertNotIn('===POCKET-BEGIN===', res.stdout)
        self.assertIn('app', res.stdout)


class TestEnvelope(PocketCase):
    def test_unknown_verb_is_error_envelope(self):
        res = self.run_pocket('frobnicate', '--json')
        data = self.json_of(res)
        self.assertFalse(data['ok'])
        self.assertIn('frobnicate', data['error'])
        self.assertEqual(res.returncode, 2)

    def test_envelope_has_verb(self):
        self.assertEqual(self.json_of(self.run_pocket('list', '--json'))['verb'], 'list')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_pocket -v 2>&1 | tail -5`
Expected: `ec2/pocket`이 없어 모든 테스트가 실패(`No such file or directory` 또는 returncode 2).

- [ ] **Step 3: `ec2/pocket` 작성**

```python
#!/usr/bin/env python3
"""pocket - manage Claude Code Remote Control servers on this box.

One server per project. A project is a directory (or a symlink to one) under
POCKET_ROOT that contains a git repository. Everything the Telegram bot can
do runs through this CLI, so the bot never needs to know the box layout.

Output contract: with --json, exactly one JSON object is printed between the
BEGIN and END sentinel lines, so a caller reading a mixed stdout (SSM merges
warnings into it) can find the payload. Without --json, human-readable text.
"""

import json
import os
import re
import subprocess
import sys

HOME = os.environ.get('POCKET_HOME', '/home/ubuntu')
ROOT = os.environ.get('POCKET_ROOT', os.path.join(HOME, 'work'))
CLAUDE_JSON = os.environ.get('POCKET_CLAUDE_JSON', os.path.join(HOME, '.claude.json'))
PROJECTS_DIR = os.environ.get('POCKET_PROJECTS_DIR', os.path.join(HOME, '.claude', 'projects'))
SUDO = os.environ.get('POCKET_SUDO', 'sudo -n')

BEGIN = '===POCKET-BEGIN==='
END = '===POCKET-END==='
UNIT = 'claude-rc@{}.service'

# The name becomes a systemd instance name, a tmux socket, a shell word and a
# directory name. This character class is the injection guard; the Lambda
# checks the same rule before the value ever reaches SSM.
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,23}$')


def valid_name(name):
    return bool(name) and NAME_RE.fullmatch(name) is not None


def project_dir(name):
    """Absolute real path of a project, or None when it is not one.

    Symlinks are resolved: trust, transcripts and session resume are all
    keyed on the real path, so everything else must use it too.
    """
    if not valid_name(name):
        return None
    path = os.path.join(ROOT, name)
    if not os.path.isdir(path):
        return None
    real = os.path.realpath(path)
    return real if os.path.isdir(os.path.join(real, '.git')) else None


def slug(path):
    """Transcript directory name Claude Code uses for a working directory."""
    return path.replace('/', '-').replace('.', '-')


def run(args, cwd=None):
    """Run a command, returning (rc, stdout). Never raises."""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout
    except Exception:
        return 1, ''


def systemctl_state(name, verb):
    rc, out = run(['systemctl', verb, UNIT.format(name)])
    value = out.strip().splitlines()[0] if out.strip() else ''
    return value or 'unknown'


def trusted(path):
    try:
        with open(CLAUDE_JSON) as fh:
            data = json.load(fh)
    except Exception:
        return False
    entry = (data.get('projects') or {}).get(path) or {}
    return bool(entry.get('hasTrustDialogAccepted'))


def last_activity(path):
    """Newest transcript mtime for this working directory, or 0."""
    d = os.path.join(PROJECTS_DIR, slug(path))
    newest = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            if f.endswith('.jsonl'):
                try:
                    newest = max(newest, int(os.path.getmtime(os.path.join(root, f))))
                except OSError:
                    pass
    return newest


def count_worktrees(path):
    rc, out = run(['git', '-C', path, 'worktree', 'list', '--porcelain'])
    # The main checkout is the first entry; only extra worktrees count.
    return max(sum(1 for line in out.splitlines() if line.startswith('worktree ')) - 1, 0)


def describe(name):
    path = project_dir(name)
    if path is None:
        return None
    _rc, branch = run(['git', '-C', path, 'rev-parse', '--abbrev-ref', 'HEAD'])
    _rc, dirty = run(['git', '-C', path, 'status', '--porcelain'])
    return {
        'name': name,
        'path': path,
        'unit': systemctl_state(name, 'is-active'),
        'enabled': systemctl_state(name, 'is-enabled') == 'enabled',
        'trusted': trusted(path),
        'branch': branch.strip() or '?',
        'dirty': bool(dirty.strip()),
        'last_activity': last_activity(path),
        'worktrees': count_worktrees(path),
    }


def list_projects():
    try:
        names = sorted(os.listdir(ROOT))
    except OSError:
        return []
    out = []
    for name in names:
        rec = describe(name)
        if rec is not None:
            out.append(rec)
    return out


def envelope(verb, data=None, error=None):
    return {'ok': error is None, 'verb': verb, 'data': data or {}, 'error': error}


def emit(obj, as_json):
    if as_json:
        print(BEGIN)
        print(json.dumps(obj, ensure_ascii=False))
        print(END)
        return
    if not obj['ok']:
        print(f"error: {obj['error']}")
        return
    data = obj['data']
    for p in data.get('projects', []):
        dot = '*' if p['unit'] == 'active' else ' '
        print(f"{dot} {p['name']:<24} {p['unit']:<10} {p['branch']:<20} "
              f"trees={p['worktrees']} trusted={p['trusted']}")


def cmd_list(args):
    return envelope('list', {'projects': list_projects()})


def cmd_check_name(args):
    name = args[0] if args else ''
    if valid_name(name):
        return envelope('check-name', {'name': name})
    return envelope('check-name', error='이름은 소문자·숫자·하이픈 1~24자, 첫 글자는 소문자나 숫자여야 해요.')


VERBS = {
    'list': cmd_list,
    'check-name': cmd_check_name,
}


def main(argv):
    as_json = '--json' in argv
    argv = [a for a in argv if a != '--json']
    verb = argv[0] if argv else 'list'
    handler = VERBS.get(verb)
    if handler is None:
        emit(envelope(verb, error=f'알 수 없는 명령: {verb}'), as_json)
        return 2
    try:
        result = handler(argv[1:])
    except Exception as exc:  # noqa: BLE001 - the CLI must always answer
        result = envelope(verb, error=f'{type(exc).__name__}: {exc}')
    emit(result, as_json)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: 실행 권한과 테스트**

Run: `chmod +x ec2/pocket && python3 -m unittest tests.test_pocket -v 2>&1 | tail -3`
Expected: 모든 테스트 통과(`OK`).

- [ ] **Step 5: 커밋**

```bash
git add ec2/pocket tests/test_pocket.py
git commit -m "[feat] 박스 CLI pocket 추가 — 프로젝트 목록과 JSON 출력 규약"
```

---

### Task 3: `pocket` — `up` / `down` / `trust` / `status`

**Files:**
- Modify: `ec2/pocket`
- Modify: `tests/test_pocket.py`
- Modify: `ec2/claude-rc.sudoers`

**Interfaces:**
- Consumes: Task 2의 `valid_name`, `project_dir`, `describe`, `envelope`, `emit`, `run`, `SUDO`
- Produces:
  - `pocket up <p>`: 신뢰·디스크·동시 서버 수를 확인하고 `systemctl enable` + `start`. 실패 사유를 `error`에 한글로
  - `pocket down <p>`: `systemctl disable` + `stop`
  - `pocket trust <p>`: 대화형 수락 자동화. 성공 여부를 `data.trusted`로
  - `pocket status`: `{"servers": [...], "mem": {"total_mb", "available_mb"}, "disk": {"free_gb"}, "auth": {"creds": bool, "token_in_env": bool}, "max_servers": int}`
  - 환경변수: `POCKET_MAX_SERVERS`(기본 2), `POCKET_MIN_FREE_GB`(기본 3)

- [ ] **Step 1: 테스트 추가 (RED)**

`tests/test_pocket.py`에 추가. 가짜 `systemctl`은 Task 2의 것을 그대로 쓰고(호출을 `systemctl.log`에 남긴다), 가짜 `claude`와 `tmux`를 새로 만든다.

```python
FAKE_TMUX = """#!/bin/sh
echo "$@" >> "$FAKE_STATE_DIR/tmux.log"
case "$1" in
  -L) shift; shift ;;
esac
case "$*" in
  *"capture-pane"*) cat "$FAKE_STATE_DIR/pane.txt" 2>/dev/null ;;
  *) : ;;
esac
"""


class TestUpDown(PocketCase):
    def test_up_refuses_unknown_project(self):
        data = self.json_of(self.run_pocket('up', 'nope', '--json'))
        self.assertFalse(data['ok'])
        self.assertIn('없', data['error'])

    def test_up_refuses_invalid_name(self):
        data = self.json_of(self.run_pocket('up', 'Bad Name', '--json'))
        self.assertFalse(data['ok'])
        self.assertEqual((self.state / 'systemctl.log').exists(), False)

    def test_up_refuses_untrusted(self):
        self.make_project('app', trusted=False)
        data = self.json_of(self.run_pocket('up', 'app', '--json'))
        self.assertFalse(data['ok'])
        self.assertIn('신뢰', data['error'])

    def test_up_enables_and_starts(self):
        self.make_project('app', trusted=True)
        data = self.json_of(self.run_pocket('up', 'app', '--json'))
        self.assertTrue(data['ok'], data)
        log = (self.state / 'systemctl.log').read_text()
        self.assertIn('enable claude-rc@app.service', log)
        self.assertIn('start claude-rc@app.service', log)

    def test_up_refuses_when_at_capacity(self):
        self.make_project('a', trusted=True, unit='active')
        self.make_project('b', trusted=True, unit='active')
        self.make_project('c', trusted=True)
        data = self.json_of(self.run_pocket('up', 'c', '--json', POCKET_MAX_SERVERS='2'))
        self.assertFalse(data['ok'])
        self.assertIn('2', data['error'])

    def test_up_refuses_when_disk_low(self):
        self.make_project('app', trusted=True)
        data = self.json_of(self.run_pocket('up', 'app', '--json', POCKET_MIN_FREE_GB='999999'))
        self.assertFalse(data['ok'])
        self.assertIn('디스크', data['error'])

    def test_down_disables_and_stops(self):
        self.make_project('app', trusted=True, unit='active', enabled=True)
        data = self.json_of(self.run_pocket('down', 'app', '--json'))
        self.assertTrue(data['ok'])
        log = (self.state / 'systemctl.log').read_text()
        self.assertIn('disable claude-rc@app.service', log)
        self.assertIn('stop claude-rc@app.service', log)

    def test_down_unknown_project_is_error(self):
        self.assertFalse(self.json_of(self.run_pocket('down', 'nope', '--json'))['ok'])


class TestStatus(PocketCase):
    def test_status_shape(self):
        self.make_project('app', trusted=True, unit='active')
        d = self.json_of(self.run_pocket('status', '--json'))['data']
        self.assertEqual([s['name'] for s in d['servers']], ['app'])
        self.assertIn('available_mb', d['mem'])
        self.assertIn('free_gb', d['disk'])
        self.assertIn('creds', d['auth'])
        self.assertEqual(d['max_servers'], 2)

    def test_status_reports_token_in_env(self):
        (self.home / '.claude').mkdir(exist_ok=True)
        (self.home / '.claude' / '.env').write_text('export CLAUDE_CODE_OAUTH_TOKEN=x\\n')
        d = self.json_of(self.run_pocket('status', '--json'))['data']
        self.assertTrue(d['auth']['token_in_env'])

    def test_status_reports_missing_credentials(self):
        d = self.json_of(self.run_pocket('status', '--json'))['data']
        self.assertFalse(d['auth']['creds'])
```

`pocket trust`는 대화형 화면에 의존하므로 단위 테스트에서는 **호출 형태만** 검증한다.

```python
class TestTrust(PocketCase):
    def test_trust_refuses_unknown_project(self):
        self.assertFalse(self.json_of(self.run_pocket('trust', 'nope', '--json'))['ok'])

    def test_trust_reports_already_trusted_without_tmux(self):
        self.make_project('app', trusted=True)
        data = self.json_of(self.run_pocket('trust', 'app', '--json'))
        self.assertTrue(data['ok'])
        self.assertTrue(data['data']['trusted'])
        self.assertFalse((self.state / 'tmux.log').exists())

    def test_trust_drives_tmux_then_rechecks(self):
        self.make_project('app', trusted=False)
        write_exec(self.bin / 'tmux', FAKE_TMUX)
        data = self.json_of(self.run_pocket('trust', 'app', '--json', POCKET_TRUST_WAIT='0'))
        log = (self.state / 'tmux.log').read_text()
        self.assertIn('new-session', log)
        self.assertIn('send-keys', log)
        self.assertFalse(data['ok'])          # the fake never writes the trust key
        self.assertIn('신뢰', data['error'])
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_pocket -v 2>&1 | tail -5`
Expected: `up`/`down`/`trust`/`status` 동사가 없어 `알 수 없는 명령` 봉투가 돌아오고 단정이 깨진다.

- [ ] **Step 3: 구현**

`ec2/pocket`에 아래를 추가하고 `VERBS`에 등록한다.

```python
MAX_SERVERS = int(os.environ.get('POCKET_MAX_SERVERS', '2'))
MIN_FREE_GB = float(os.environ.get('POCKET_MIN_FREE_GB', '3'))
TRUST_WAIT = float(os.environ.get('POCKET_TRUST_WAIT', '12'))
CREDS = os.path.join(HOME, '.claude', '.credentials.json')
CLAUDE_ENV = os.path.join(HOME, '.claude', '.env')


def sudo_systemctl(*args):
    """systemctl through sudo, matching the narrow sudoers rule."""
    cmd = SUDO.split() + ['systemctl', *args] if SUDO else ['systemctl', *args]
    return run(cmd)


def free_gb(path):
    try:
        st = os.statvfs(path)
        return (st.f_bavail * st.f_frsize) / (1024 ** 3)
    except OSError:
        return 0.0


def mem_info():
    info = {'total_mb': 0, 'available_mb': 0}
    try:
        with open('/proc/meminfo') as fh:
            for line in fh:
                if line.startswith('MemTotal:'):
                    info['total_mb'] = int(line.split()[1]) // 1024
                elif line.startswith('MemAvailable:'):
                    info['available_mb'] = int(line.split()[1]) // 1024
    except OSError:
        pass
    return info


def running_count(projects):
    return sum(1 for p in projects if p['unit'] == 'active')


def cmd_up(args):
    name = args[0] if args else ''
    if not valid_name(name):
        return envelope('up', error='이름은 소문자·숫자·하이픈 1~24자여야 해요.')
    path = project_dir(name)
    if path is None:
        return envelope('up', error=f'{name} 프로젝트가 없어요. ~/work 아래에 폴더나 링크를 두세요.')
    if not trusted(path):
        return envelope('up', error=f'{name} 는 아직 신뢰되지 않았어요. pocket trust {name} 를 먼저 하세요.')
    free = free_gb(path)
    if free < MIN_FREE_GB:
        return envelope('up', error=f'디스크 여유가 {free:.1f}GB뿐이에요 (최소 {MIN_FREE_GB}GB).')
    projects = list_projects()
    if running_count(projects) >= MAX_SERVERS and systemctl_state(name, 'is-active') != 'active':
        return envelope('up', error=f'동시에 켤 수 있는 서버는 {MAX_SERVERS}개예요. 하나를 먼저 끄세요.')
    sudo_systemctl('enable', UNIT.format(name))
    sudo_systemctl('start', UNIT.format(name))
    return envelope('up', {'name': name, 'unit': systemctl_state(name, 'is-active')})


def cmd_down(args):
    name = args[0] if args else ''
    if project_dir(name) is None:
        return envelope('down', error=f'{name} 프로젝트가 없어요.')
    sudo_systemctl('disable', UNIT.format(name))
    sudo_systemctl('stop', UNIT.format(name))
    return envelope('down', {'name': name, 'unit': systemctl_state(name, 'is-active')})


def cmd_status(args):
    projects = list_projects()
    token_in_env = False
    try:
        with open(CLAUDE_ENV) as fh:
            token_in_env = 'CLAUDE_CODE_OAUTH_TOKEN' in fh.read()
    except OSError:
        pass
    return envelope('status', {
        'servers': [p for p in projects if p['unit'] == 'active' or p['enabled']],
        'projects': len(projects),
        'mem': mem_info(),
        'disk': {'free_gb': round(free_gb(ROOT), 1)},
        'auth': {'creds': os.path.exists(CREDS), 'token_in_env': token_in_env},
        'max_servers': MAX_SERVERS,
    })


def cmd_trust(args):
    """Accept the workspace trust dialog for a project, non-interactively.

    Claude Code stores trust per git repository root in ~/.claude.json and a
    Remote Control server refuses to start without it. There is no flag for
    this, so drive the interactive dialog in a scratch tmux socket and verify
    the key afterwards: other Claude processes rewrite the same file, so a
    blind edit can be lost.
    """
    import time
    name = args[0] if args else ''
    path = project_dir(name)
    if path is None:
        return envelope('trust', error=f'{name} 프로젝트가 없어요.')
    if trusted(path):
        return envelope('trust', {'name': name, 'trusted': True, 'already': True})
    sock = ['tmux', '-L', 'pocket-trust']
    run(sock + ['kill-server'])
    run(sock + ['new-session', '-d', '-s', 'trust', '-x', '120', '-y', '40', '-c', path, 'claude'])
    time.sleep(TRUST_WAIT)
    run(sock + ['send-keys', '-t', 'trust', 'Down'])
    run(sock + ['send-keys', '-t', 'trust', 'Enter'])
    time.sleep(3)
    run(sock + ['send-keys', '-t', 'trust', 'Enter'])      # settings warning, if any
    time.sleep(2)
    run(sock + ['send-keys', '-t', 'trust', '/exit', 'Enter'])
    time.sleep(3)
    run(sock + ['kill-server'])
    ok = trusted(path)
    if ok:
        return envelope('trust', {'name': name, 'trusted': True, 'already': False})
    return envelope('trust', error=f'{name} 신뢰 수락에 실패했어요. SSM 셸에서 cd {path} && claude 로 직접 수락하세요.')
```

`emit`의 사람용 출력에 `status`·`up`·`down` 분기를 더한다(각 한 줄이면 충분하다).

- [ ] **Step 4: sudoers 확장**

`ec2/claude-rc.sudoers`의 규칙을 바꾼다(정규식 형태 유지, F: sudo 1.9.15):

```
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl ^(start|stop|restart|enable|disable|reset-failed) claude-rc@[a-z0-9][a-z0-9-]{0,23}\.service$
```

기존 규칙과 달리 유닛 이름에 `.service` 접미사가 붙는다(`pocket`이 항상 붙여 부른다). 설치는 `install.sh`의 `visudo -cf` 검증을 그대로 거친다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `python3 -m unittest tests.test_pocket -v 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 6: 커밋**

```bash
git add ec2/pocket ec2/claude-rc.sudoers tests/test_pocket.py
git commit -m "[feat] pocket up/down/trust/status 추가와 sudoers 확장"
```

---

### Task 4: `pocket` — `trees` / `prune`

**Files:**
- Modify: `ec2/pocket`
- Modify: `tests/test_pocket.py`

**Interfaces:**
- Consumes: Task 2·3의 헬퍼
- Produces:
  - `pocket trees <p>` → `data.trees = [{"path", "branch", "dirty", "unpushed", "in_use"}]`
  - `pocket prune <p> [--dry-run]` → `data = {"removed": [...], "kept": [{"path", "reason"}]}`
  - 삭제 조건: 미커밋 없음 **그리고** 미푸시 커밋 없음 **그리고** 그 경로를 cwd로 쓰는 claude 프로세스 없음. 세 조건 중 하나라도 걸리면 `kept`에 사유와 함께 남긴다
  - 브랜치는 병합된 경우에만 지운다

- [ ] **Step 1: 테스트 추가 (RED)**

가짜 `git`을 확장해 `worktree list --porcelain`, `status --porcelain`, `log @{u}..HEAD`, `branch --merged`, `worktree remove`를 흉내 낸다.

```python
FAKE_GIT_TREES = """#!/bin/sh
echo "$@" >> "$FAKE_STATE_DIR/git.log"
case "$*" in
  *"worktree list --porcelain"*) cat "$FAKE_STATE_DIR/worktrees.txt" 2>/dev/null ;;
  *"rev-parse --abbrev-ref HEAD"*) echo "${FAKE_BRANCH:-main}" ;;
  *"status --porcelain"*)
      case "$*" in *dirty*) echo " M f.txt" ;; *) : ;; esac ;;
  *"log --oneline @{u}..HEAD"*)
      case "$*" in *unpushed*) echo "abc1234 wip" ;; *) : ;; esac ;;
  *"branch --merged"*) echo "  worktree-clean" ;;
  *"worktree remove"*) echo "removed" ;;
  *) : ;;
esac
"""


class TestTrees(PocketCase):
    def setup_trees(self, names):
        d = self.make_project('app', trusted=True)
        write_exec(self.bin / 'git', FAKE_GIT_TREES)
        lines = [f'worktree {d}', 'branch refs/heads/main', '']
        for n in names:
            wt = d / '.claude' / 'worktrees' / n
            wt.mkdir(parents=True)
            lines += [f'worktree {wt}', f'branch refs/heads/worktree-{n}', '']
        (self.state / 'worktrees.txt').write_text('\\n'.join(lines))
        return d

    def test_trees_lists_only_extra_worktrees(self):
        self.setup_trees(['clean', 'dirty'])
        trees = self.json_of(self.run_pocket('trees', 'app', '--json'))['data']['trees']
        self.assertEqual(sorted(t['branch'] for t in trees),
                         ['worktree-clean', 'worktree-dirty'])

    def test_trees_flags_dirty_and_unpushed(self):
        self.setup_trees(['clean', 'dirty', 'unpushed'])
        by = {t['branch']: t for t in self.json_of(self.run_pocket('trees', 'app', '--json'))['data']['trees']}
        self.assertTrue(by['worktree-dirty']['dirty'])
        self.assertTrue(by['worktree-unpushed']['unpushed'])
        self.assertFalse(by['worktree-clean']['dirty'])
        self.assertFalse(by['worktree-clean']['unpushed'])

    def test_prune_removes_only_clean_trees(self):
        self.setup_trees(['clean', 'dirty', 'unpushed'])
        data = self.json_of(self.run_pocket('prune', 'app', '--json'))['data']
        self.assertEqual([os.path.basename(p) for p in data['removed']], ['clean'])
        kept = {os.path.basename(k['path']): k['reason'] for k in data['kept']}
        self.assertIn('미커밋', kept['dirty'])
        self.assertIn('미푸시', kept['unpushed'])

    def test_prune_dry_run_removes_nothing(self):
        self.setup_trees(['clean'])
        data = self.json_of(self.run_pocket('prune', 'app', '--dry-run', '--json'))['data']
        self.assertEqual(data['removed'], [])
        self.assertIn('clean', str(data['would_remove']))
        self.assertNotIn('worktree remove', (self.state / 'git.log').read_text())

    def test_prune_keeps_tree_in_use(self):
        # POCKET_CWDS stands in for the /proc scan of live claude processes.
        d = self.setup_trees(['clean'])
        data = self.json_of(self.run_pocket(
            'prune', 'app', '--json',
            POCKET_CWDS=str(d / '.claude' / 'worktrees' / 'clean')))['data']
        self.assertEqual(data['removed'], [])
        self.assertIn('사용 중', data['kept'][0]['reason'])
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_pocket -k Trees -v 2>&1 | tail -5`
Expected: `알 수 없는 명령: trees`.

- [ ] **Step 3: 구현**

```python
def claude_cwds():
    """Working directories of live claude processes.

    POCKET_CWDS (colon-separated) overrides the scan in tests.
    """
    override = os.environ.get('POCKET_CWDS')
    if override is not None:
        # realpath to match the /proc branch: on macOS a temp dir is reached
        # through a symlink, so a raw string would never compare equal.
        return [os.path.realpath(p) for p in override.split(':') if p]
    out = []
    for pid in os.listdir('/proc') if os.path.isdir('/proc') else []:
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/comm') as fh:
                if fh.read().strip() != 'claude':
                    continue
            out.append(os.path.realpath(f'/proc/{pid}/cwd'))
        except OSError:
            continue
    return out


def worktrees_of(path):
    """Extra worktrees of a repo (the main checkout is excluded)."""
    _rc, out = run(['git', '-C', path, 'worktree', 'list', '--porcelain'])
    entries, cur = [], {}
    for line in out.splitlines():
        if line.startswith('worktree '):
            if cur:
                entries.append(cur)
            cur = {'path': line.split(' ', 1)[1].strip()}
        elif line.startswith('branch '):
            cur['branch'] = line.split('/')[-1].strip()
    if cur:
        entries.append(cur)
    return [e for e in entries if os.path.realpath(e.get('path', '')) != os.path.realpath(path)]


def unpushed_commits(wt):
    """True when the worktree holds commits no remote has.

    Fail safe. A failed git call prints nothing and exits non-zero, which
    looks exactly like "no commits ahead"; treating that as clean would let
    prune delete real work, and git worktree remove does not protect
    commits. A kept worktree only costs disk.
    """
    rc, out = run(['git', '-C', wt, 'log', '--oneline', '@{u}..HEAD'])
    if rc == 0:
        return bool(out.strip())
    rc, out = run(['git', '-C', wt, 'log', '--oneline', 'origin/HEAD..HEAD'])
    if rc == 0:
        return bool(out.strip())
    return True


def tree_state(entry, cwds):
    wt = entry['path']
    _rc, dirty = run(['git', '-C', wt, 'status', '--porcelain'])
    return {
        'path': wt,
        'branch': entry.get('branch', '?'),
        'dirty': bool(dirty.strip()),
        'unpushed': unpushed_commits(wt),
        'in_use': os.path.realpath(wt) in cwds,
    }


def cmd_trees(args):
    name = args[0] if args else ''
    path = project_dir(name)
    if path is None:
        return envelope('trees', error=f'{name} 프로젝트가 없어요.')
    cwds = claude_cwds()
    trees = [tree_state(e, cwds) for e in worktrees_of(path)]
    return envelope('trees', {'name': name, 'trees': trees})


def cmd_prune(args):
    dry = '--dry-run' in args
    args = [a for a in args if a != '--dry-run']
    name = args[0] if args else ''
    path = project_dir(name)
    if path is None:
        return envelope('prune', error=f'{name} 프로젝트가 없어요.')
    cwds = claude_cwds()
    removed, kept, would = [], [], []
    _rc, merged_out = run(['git', '-C', path, 'branch', '--merged'])
    # git marks the current branch with '*' and one checked out in another
    # worktree with '+'; every candidate here is the latter.
    merged = {b.strip().lstrip('*+ ') for b in merged_out.splitlines()}
    for entry in worktrees_of(path):
        st = tree_state(entry, cwds)
        reason = ('미커밋 변경이 있어요' if st['dirty'] else
                  '미푸시 커밋이 있어요' if st['unpushed'] else
                  '세션이 사용 중이에요' if st['in_use'] else None)
        if reason:
            kept.append({'path': st['path'], 'branch': st['branch'], 'reason': reason})
            continue
        if dry:
            would.append(st['path'])
            continue
        rc, _out = run(['git', '-C', path, 'worktree', 'remove', st['path']])
        if rc != 0:
            kept.append({'path': st['path'], 'branch': st['branch'], 'reason': '삭제 실패'})
            continue
        removed.append(st['path'])
        if st['branch'] in merged:
            run(['git', '-C', path, 'branch', '-d', st['branch']])
    return envelope('prune', {'name': name, 'removed': removed,
                              'would_remove': would, 'kept': kept})
```

`VERBS`에 `trees`·`prune`을 등록하고 사람용 출력 분기를 더한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m unittest tests.test_pocket -v 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: 커밋**

```bash
git add ec2/pocket tests/test_pocket.py
git commit -m "[feat] pocket trees/prune 추가 — 미커밋·미푸시·사용 중 워크트리 보호"
```

---

### Task 5: 래퍼·유닛·slice 일반화

**Files:**
- Modify: `ec2/claude-rc-wrap.sh`
- Modify: `ec2/claude-rc@.service`
- Create: `ec2/claude-rc.slice`
- Modify: `ec2/install.sh`
- Modify: `tests/test_claude_rc_wrap.sh`

**Interfaces:**
- Consumes: Task 1의 실측값(`--spawn worktree` 동작), Task 2의 `~/work` 관례
- Produces: 유닛 `claude-rc@<프로젝트>`가 `~/work/<프로젝트>`의 realpath에서 서버를 띄운다. 래퍼 함수 `rc_workdir`(realpath 해석), `rc_command`(옵션 반영), `rc_trusted`(신뢰 확인)

- [ ] **Step 1: 래퍼 테스트 갱신 (RED)**

`tests/test_claude_rc_wrap.sh`에서 gamer4 전제를 지우고 아래를 단정한다.

```bash
export POCKET_ROOT="$TMPDIR_FAKE/work"
mkdir -p "$POCKET_ROOT/myapp/.git"
assert_eq "$(rc_workdir myapp)" "$(cd "$POCKET_ROOT/myapp" && pwd -P)" "workdir resolves under POCKET_ROOT"

CMD=$(rc_command myapp)
assert_contains "$CMD" 'claude remote-control' "server mode"
assert_contains "$CMD" '--name "myapp"' "session name is the project name"
assert_contains "$CMD" '--spawn worktree' "spawn mode defaults to worktree"
assert_contains "$CMD" '--capacity 3' "capacity is 1 + POCKET_SESSIONS(2)"
assert_contains "$CMD" '--permission-mode default' "permission mode"
assert_contains "$CMD" '--no-chrome' "chrome off"

POCKET_SESSIONS=4 CMD2=$(rc_command myapp)
assert_contains "$CMD2" '--capacity 5' "capacity follows POCKET_SESSIONS"

POCKET_SPAWN=same-dir CMD3=$(rc_command myapp)
assert_contains "$CMD3" '--spawn same-dir' "spawn mode can be overridden per project"

grep -c 'tmux -L "rc-$name"' ../ec2/claude-rc-wrap.sh   # ≥ 3, 기존 단정 유지
```

신뢰 확인 단정도 더한다: `rc_trusted`가 `POCKET_CLAUDE_JSON`의 키를 읽어 0/1을 돌려주고, 신뢰가 없으면 `main`이 `exit 1`.

- [ ] **Step 2: 실패 확인**

Run: `bash tests/test_claude_rc_wrap.sh`
Expected: `rc_workdir`가 아직 `ops` 분기를 쓰고 `rc_command`가 `gamer4-` 접두사를 붙여 실패.

- [ ] **Step 3: 래퍼 수정**

`ec2/claude-rc-wrap.sh`에서 `REPO`·`WORKTREES` 상수를 지우고 아래로 바꾼다(나머지 가드·tmux 블록은 그대로).

```bash
POCKET_ROOT=${POCKET_ROOT:-/home/ubuntu/work}
CLAUDE_JSON=${POCKET_CLAUDE_JSON:-/home/ubuntu/.claude.json}

# Trust, transcripts and session resume are all keyed on the real path, so a
# symlinked project must resolve before anything else uses the path.
rc_workdir() {
  local p="$POCKET_ROOT/$1"
  [ -d "$p" ] || return 1
  (cd "$p" && pwd -P)
}

# A server refuses to start in an untrusted directory and systemd would then
# restart it until the start limit trips, so check first and fail loudly.
rc_trusted() {
  python3 - "$CLAUDE_JSON" "$1" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
entry = (data.get('projects') or {}).get(sys.argv[2]) or {}
sys.exit(0 if entry.get('hasTrustDialogAccepted') else 1)
PY
}

rc_command() {
  printf 'claude remote-control --name "%s" --spawn %s --capacity %s --permission-mode default --no-chrome' \
    "$1" "${POCKET_SPAWN:-worktree}" "$(( 1 + ${POCKET_SESSIONS:-2} ))"
}
```

`main`에서 작업 디렉터리 확인 뒤에 신뢰 확인을 넣는다.

```bash
  if ! rc_trusted "$dir"; then
    logger -t claude-rc-wrap "workspace not trusted: $dir (run: pocket trust $name)"
    exit 1
  fi
```

- [ ] **Step 4: 유닛과 slice**

`ec2/claude-rc@.service`에서 `EnvironmentFile` 줄을 바꾸고 `Slice`를 더한다.

```ini
EnvironmentFile=-/home/ubuntu/.config/pocket-claude/projects/%i.env
Slice=claude-rc.slice
```

`ec2/claude-rc.slice` 신규:

```ini
[Unit]
Description=Claude Code Remote Control servers

[Slice]
# Aggregate cap for every claude-rc@ instance. MemoryHigh throttles first so a
# build slows down instead of dying; MemoryMax is the hard stop that protects
# the SSM agent, which is the only remote recovery path.
MemoryHigh=3G
MemoryMax=3.5G
```

- [ ] **Step 5: install.sh 갱신**

- `/home/ubuntu/work`, `/home/ubuntu/.config/pocket-claude/projects` 생성(소유자 ubuntu, 0755/0700)
- `pocket`을 `/home/ubuntu/bin/pocket`으로 설치(0755)
- `claude-rc.slice` 설치 + `daemon-reload`
- `worktrees` 디렉터리 생성 제거, `claude-rc@ops` enable 제거(이행 스크립트가 정한다)
- cron의 `idle-watch.sh` 등록을 **추가하지 않는다**(수동 정지 결정). 기존 등록이 있으면 제거

- [ ] **Step 6: 테스트와 문법 검사**

Run: `bash -n ec2/claude-rc-wrap.sh ec2/install.sh && bash tests/test_claude_rc_wrap.sh && python3 -m unittest tests.test_pocket 2>&1 | tail -2`
Expected: 오류 없음, `all passed`, `OK`

- [ ] **Step 7: 커밋**

```bash
git add ec2/claude-rc-wrap.sh ec2/claude-rc@.service ec2/claude-rc.slice ec2/install.sh tests/test_claude_rc_wrap.sh
git commit -m "[feat] 래퍼와 유닛을 프로젝트 단위로 일반화하고 메모리 slice 추가"
```

---

### Task 6: 워크트리 `.env` 복사 훅

**Files:**
- Create: `ec2/worktree-env-hook.sh`
- Modify: `ec2/claude-settings.json`
- Create: `tests/test_worktree_env_hook.sh`

**Interfaces:**
- Consumes: Task 1 Step 3의 실측(훅이 워크트리 안에서 도는지)
- Produces: `SessionStart` 훅. 현재 폴더가 링크된 워크트리이고 본 체크아웃에 있는 git 무시 파일이 없으면 복사한다. **표준출력에 아무것도 쓰지 않는다**(출력은 세션 맥락으로 들어간다)

- [ ] **Step 1: 테스트 작성 (RED)**

`tests/test_worktree_env_hook.sh`. 실제 `git init` + `git worktree add`로 리포를 만든다(맥·박스 모두 git이 있다).

```bash
#!/bin/bash
# Exercises the SessionStart hook that copies ignored files into a worktree.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
HOOK=$HERE/../ec2/worktree-env-hook.sh
FAILS=0

assert_eq() {
  if [ "$1" = "$2" ]; then echo "ok   $3"
  else echo "FAIL $3"; echo "     expected: $2"; echo "     got:      $1"; FAILS=$((FAILS + 1)); fi
}

setup() {
  TMP=$(mktemp -d)
  MAIN=$TMP/main
  mkdir -p "$MAIN"
  git -C "$MAIN" init -q
  git -C "$MAIN" config user.email t@example.com
  git -C "$MAIN" config user.name test
  printf '.env\n.env.local\n.vercel/\n' > "$MAIN/.gitignore"
  echo hello > "$MAIN/README.md"
  git -C "$MAIN" add -A
  git -C "$MAIN" commit -qm init
  printf 'SECRET=1\n' > "$MAIN/.env"
  printf 'LOCAL=1\n' > "$MAIN/.env.local"
  mkdir -p "$MAIN/.vercel"; printf '{}\n' > "$MAIN/.vercel/project.json"
  WT=$MAIN/.claude/worktrees/w1
  git -C "$MAIN" worktree add -q -b worktree-w1 "$WT" >/dev/null 2>&1
}

# 1. main checkout: does nothing
setup
(cd "$MAIN" && bash "$HOOK")
assert_eq "$(ls "$MAIN" | grep -c '^\.env$' || true)" "1" "main checkout is untouched"

# 2. worktree: ignored files are copied
setup
OUT=$( cd "$WT" && bash "$HOOK" )
assert_eq "$(cat "$WT/.env" 2>/dev/null)" "SECRET=1" "copies .env"
assert_eq "$(cat "$WT/.env.local" 2>/dev/null)" "LOCAL=1" "copies .env.local"
assert_eq "$([ -f "$WT/.vercel/project.json" ] && echo yes || echo no)" "yes" "copies .vercel/project.json"

# 3. hook prints nothing (its stdout would become session context)
assert_eq "$OUT" "" "hook output is empty"

# 4. existing files are not overwritten
setup
printf 'MINE=1\n' > "$WT/.env"
(cd "$WT" && bash "$HOOK")
assert_eq "$(cat "$WT/.env")" "MINE=1" "does not overwrite an existing file"

# 5. tracked files are never copied
setup
rm -f "$WT/README.md"
(cd "$WT" && bash "$HOOK")
assert_eq "$([ -f "$WT/README.md" ] && echo yes || echo no)" "no" "tracked files are left alone"

# 6. outside a repo it exits quietly
setup
OUT2=$( cd "$TMP" && bash "$HOOK" ); RC=$?
assert_eq "$RC" "0" "exits 0 outside a repo"
assert_eq "$OUT2" "" "prints nothing outside a repo"

[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }
```

- [ ] **Step 2: 실패 확인**

Run: `bash tests/test_worktree_env_hook.sh`
Expected: 훅 파일이 없어 실패.

- [ ] **Step 3: 훅 작성**

```bash
#!/bin/bash
# SessionStart hook: give a worktree session the ignored files it needs.
#
# Claude Code creates session worktrees under <repo>/.claude/worktrees/ and
# copies nothing into them, so a project whose runtime needs .env fails in a
# fresh session. Copy only files git ignores, never overwrite, and print
# nothing: hook stdout becomes session context.
set -u

CWD=$(pwd -P)
GIT_COMMON=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
[ "$GIT_COMMON" = "$GIT_DIR" ] && exit 0          # main checkout: nothing to do

MAIN=$(cd "$(dirname "$GIT_COMMON")" && pwd -P)
[ -d "$MAIN" ] || exit 0

for rel in .env .env.local .env.development.local .vercel/project.json; do
  src="$MAIN/$rel"
  dst="$CWD/$rel"
  [ -f "$src" ] || continue
  [ -e "$dst" ] && continue
  git -C "$MAIN" check-ignore -q "$rel" || continue   # only ignored files
  mkdir -p "$(dirname "$dst")"
  cp -p "$src" "$dst"
done
exit 0
```

- [ ] **Step 4: settings에 등록**

`ec2/claude-settings.json`에 훅을 더하고 gamer4 전용 ask 규칙을 일반화한다.

```json
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "/home/ubuntu/bin/worktree-env-hook.sh" } ] }
    ]
  }
```

ask 목록에서 프로젝트 전용 항목(`sb_sql.py`, `yarn deploy:lambda`)을 빼고 범용 배포·파괴 명령으로 바꾼다: `sam deploy`, `cdk deploy`, `terraform apply`, `npx vercel promote|redeploy|env|firewall|api`, `gh pr merge`, `aws lambda update-function-*`, `aws ec2 stop-instances`, `docker push`, `npm publish`. deny 경로의 `~/.config/gamer4/`는 `~/.config/pocket-claude/`로.

- [ ] **Step 5: 테스트 통과 확인**

Run: `bash tests/test_worktree_env_hook.sh && python3 -m json.tool ec2/claude-settings.json > /dev/null && echo ok`
Expected: 통과, `ok`

- [ ] **Step 6: 커밋**

```bash
git add ec2/worktree-env-hook.sh ec2/claude-settings.json tests/test_worktree_env_hook.sh
git commit -m "[feat] 워크트리 세션에 무시 파일을 복사하는 훅 추가"
```

---

### Task 7: Lambda v6 — `pocket` 호출과 새 명령

**Files:**
- Modify: `src/lambda_function.py`
- Modify: `tests/test_lambda_function.py`

**Interfaces:**
- Consumes: Task 2~4의 CLI 동사와 JSON 봉투·구분선
- Produces:
  - `pocket_script(verb, *args) -> str` — `sudo -u ubuntu -H /home/ubuntu/bin/pocket <verb> <args> --json`를 POSIX sh로 감싼 문자열
  - `parse_pocket(out) -> dict` — 마지막 완전한 봉투. 구분선이 없거나 잘리면 `{'ok': False, 'error': ...}`
  - `format_projects(rows) -> str`, `format_status(data, uptime) -> str`, `format_trees(data) -> str`
  - 명령: `/start /stop /status /projects /up /down /trees`. `/new /kill /rm /sessions /view`는 제거하고 HELP로 안내
  - 상수 `POCKET = '/home/ubuntu/bin/pocket'`, `NAME_RE = ^[a-z0-9][a-z0-9-]{0,23}$`, `SSM_STDOUT_LIMIT = 24000`

- [ ] **Step 1: 테스트 교체 (RED)**

기존 `/new /kill /rm /sessions` 테스트를 지우고 아래를 넣는다(발췌).

```python
class TestPocketBridge(unittest.TestCase):
    def test_script_is_posix_and_quotes_nothing_dangerous(self):
        s = lf.pocket_script('up', 'guam-go')
        self.assertIn('/home/ubuntu/bin/pocket up guam-go --json', s)
        self.assertIn('sudo -u ubuntu', s)
        self.assertNotIn('[[', s)

    def test_parse_returns_last_envelope(self):
        out = 'noise\n===POCKET-BEGIN===\n{"ok": true, "verb": "list", "data": {"projects": []}}\n===POCKET-END===\n'
        self.assertTrue(lf.parse_pocket(out)['ok'])

    def test_parse_reports_missing_envelope(self):
        r = lf.parse_pocket('boom: command not found')
        self.assertFalse(r['ok'])
        self.assertIn('응답', r['error'])

    def test_parse_reports_truncated_output(self):
        out = '===POCKET-BEGIN===\n{"ok": true, ' + 'x' * 10
        r = lf.parse_pocket(out)
        self.assertFalse(r['ok'])

    def test_format_projects_marks_running(self):
        rows = [{'name': 'a', 'unit': 'active', 'branch': 'main', 'last_activity': 0,
                 'worktrees': 2, 'trusted': True, 'enabled': True, 'dirty': False},
                {'name': 'b', 'unit': 'inactive', 'branch': 'main', 'last_activity': 0,
                 'worktrees': 0, 'trusted': False, 'enabled': False, 'dirty': False}]
        out = lf.format_projects(rows)
        self.assertIn('🟢 a', out)
        self.assertIn('⚪ b', out)
        self.assertIn('신뢰 필요', out)      # b is untrusted
```

라우팅 테스트는 `ssm_run`을 봉투 문자열로 스텁해 `/projects`·`/up`·`/down`·`/trees`가 각 동사를 부르고 오류 봉투를 사용자 문장으로 바꾸는지 본다. `/new`는 HELP로 떨어져야 한다.

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest discover -s tests -p test_lambda_function.py 2>&1 | tail -5`
Expected: `pocket_script`·`parse_pocket` 부재로 ERROR.

- [ ] **Step 3: 구현**

경로·접두사 상수(`REPO`, `WORKTREES`, `SESSION_PREFIX`, `OPS`)와 `workdir`, `new_session_script`, `kill_session_script`, `rm_session_script`, `sessions_script`, `cmd_new`, `cmd_kill`, `cmd_rm`, `cmd_sessions`를 지우고 아래로 대체한다.

```python
POCKET = '/home/ubuntu/bin/pocket'
BEGIN, END = '===POCKET-BEGIN===', '===POCKET-END==='
SSM_STDOUT_LIMIT = 24000


def pocket_script(verb, *args):
    """POSIX sh that runs one pocket verb as ubuntu and prints its envelope."""
    words = ' '.join([verb, *args]).strip()
    return f'sudo -u ubuntu -H {POCKET} {words} --json 2>&1 || true\n'


def parse_pocket(out):
    """Last complete envelope from a mixed stdout, or an error envelope."""
    if len(out) >= SSM_STDOUT_LIMIT:
        return {'ok': False, 'error': '박스 응답이 잘렸어요. 잠시 후 다시 시도하세요.'}
    if BEGIN not in out or END not in out:
        tail = out.strip()[-300:]
        return {'ok': False, 'error': f'박스에서 응답을 읽지 못했어요.\n{tail}'}
    body = out.rsplit(BEGIN, 1)[1].split(END, 1)[0]
    try:
        return json.loads(body)
    except ValueError:
        return {'ok': False, 'error': '박스 응답을 해석하지 못했어요.'}
```

공통 흐름을 한 곳에 둔다.

```python
def run_pocket(chat_id, verb, *args):
    """Run one pocket verb on the box. Returns the envelope, or None when the
    instance is not reachable (the user was already told why)."""
    if not require_running(chat_id):
        return None
    out = ssm_run(pocket_script(verb, *args), timeout=20)
    return parse_pocket(out)


def reply_envelope(chat_id, env, formatter, keyboard=None):
    if env is None:
        return
    if not env.get('ok'):
        tg_send(chat_id, f"⚠️ {env.get('error') or '알 수 없는 오류'}")
        return
    tg_send(chat_id, formatter(env['data']), keyboard)


def format_projects(rows):
    if not rows:
        return '프로젝트가 없어요. 박스의 ~/work 아래에 리포 폴더나 링크를 두세요.'
    now = time.time()
    lines = []
    for p in rows:
        dot = '🟢' if p['unit'] == 'active' else '⚪'
        last = format_age(p['last_activity'], now) if p['last_activity'] else '대화 없음'
        line = f"{dot} {p['name']}  [{p['branch']}]  {last}"
        if p['worktrees']:
            line += f"  워크트리 {p['worktrees']}"
        if not p['trusted']:
            line += '\n   ⚠️ 신뢰 필요 — SSM 셸에서 pocket trust ' + p['name']
        lines.append(line)
    return '\n'.join(lines)


def format_status(data, uptime):
    mem, disk, auth = data['mem'], data['disk'], data['auth']
    lines = [f"✅ EC2 running ({uptime})"]
    servers = data.get('servers') or []
    if servers:
        for s in servers:
            dot = '🟢' if s['unit'] == 'active' else '⚪'
            lines.append(f"{dot} {s['name']} {s['unit']}")
    else:
        lines.append('⚪ 켜진 서버 없음')
    lines.append(f"프로젝트 {data['projects']}개 · 동시 한도 {data['max_servers']}")
    lines.append(f"메모리 여유 {mem['available_mb']}MB / {mem['total_mb']}MB · 디스크 여유 {disk['free_gb']}GB")
    if auth['token_in_env']:
        lines.append('❌ ~/.claude/.env 에 CLAUDE_CODE_OAUTH_TOKEN 이 남아 있어요 (Remote Control 차단)')
    elif not auth['creds']:
        lines.append('❌ claude.ai 로그인이 없어요 — SSM 셸에서 claude auth login')
    else:
        lines.append('✅ 인증 OK')
    return '\n'.join(lines)


def format_trees(data):
    trees = data.get('trees') or []
    if not trees:
        return f"{data['name']}: 세션 워크트리가 없어요."
    lines = [f"{data['name']} 워크트리 {len(trees)}개"]
    for t in trees:
        marks = []
        if t['dirty']:
            marks.append('미커밋')
        if t['unpushed']:
            marks.append('미푸시')
        if t['in_use']:
            marks.append('사용 중')
        lines.append(f"· {t['branch']}  {' '.join(marks) or '정리 가능'}")
    return '\n'.join(lines)


def cmd_projects(chat_id, arg):
    env = run_pocket(chat_id, 'list')
    if env is None:
        return
    if not env.get('ok'):
        tg_send(chat_id, f"⚠️ {env.get('error')}")
        return
    rows = env['data']['projects']
    tg_send(chat_id, format_projects(rows), keyboard_for(rows))


def cmd_up(chat_id, arg):
    err = validate_name(arg)
    if err:
        tg_send(chat_id, f'⚠️ {err}')
        return
    env = run_pocket(chat_id, 'up', arg)
    reply_envelope(chat_id, env, lambda d: f"🔄 {d['name']} 기동 ({d['unit']}). 앱 Code 탭에서 확인하세요.")


def cmd_down(chat_id, arg):
    err = validate_name(arg)
    if err:
        tg_send(chat_id, f'⚠️ {err}')
        return
    env = run_pocket(chat_id, 'down', arg)
    reply_envelope(chat_id, env, lambda d: f"⏹ {d['name']} 정지 ({d['unit']}). 부팅 자동 기동도 해제했어요.")


def cmd_trees(chat_id, arg):
    err = validate_name(arg)
    if err:
        tg_send(chat_id, f'⚠️ {err}')
        return
    env = run_pocket(chat_id, 'trees', arg)
    reply_envelope(chat_id, env, format_trees)
```

`cmd_status`는 `pocket status` 봉투에 인스턴스 가동 시간을 붙인다(가동 시간은 Lambda가 이미 안다).

```python
def cmd_status(chat_id, arg):
    state, launch = describe()
    if state != 'running':
        tg_send(chat_id, f'⚪ EC2 {state}\n   /start 로 켜세요.')
        return
    if not ssm_ready():
        tg_send(chat_id, '🔄 부팅 중 — SSM 에이전트 대기 중입니다.')
        return
    env = parse_pocket(ssm_run(pocket_script('status'), timeout=20))
    if not env.get('ok'):
        tg_send(chat_id, f"⚠️ {env.get('error')}")
        return
    uptime = format_uptime(launch, datetime.now(timezone.utc))
    tg_send(chat_id, format_status(env['data'], uptime))
```

`validate_name`은 예약 이름 `ops` 검사를 지우고 규칙을 `^[a-z0-9][a-z0-9-]{0,23}$`로 맞춘다. `HELP`와 `HANDLERS`는 `/start /stop /status /projects /up /down /trees`로 교체한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: 커밋**

```bash
git add src/lambda_function.py tests/test_lambda_function.py
git commit -m "[feat] 람다 v6 — pocket CLI 호출과 프로젝트 명령으로 교체"
```

---

### Task 8: Lambda v6 — 인라인 버튼

**Files:**
- Modify: `src/lambda_function.py`
- Modify: `tests/test_lambda_function.py`

**Interfaces:**
- Consumes: Task 7의 포맷 함수
- Produces:
  - `keyboard_for(rows) -> dict` — 프로젝트마다 `▶ 켜기`/`⏹ 끄기`, `🌳 트리` 버튼. `callback_data`는 `up:<name>` 형태로 64바이트 이내(F11)
  - `tg_send(chat_id, text, keyboard=None)`, `tg_answer_callback(cb_id)`, `tg_edit_markup(chat_id, message_id, keyboard)`
  - `handle_callback(update)` — **먼저** `answerCallbackQuery`를 부르고, chat/from ID를 검사한 뒤 해당 동사를 실행

- [ ] **Step 1: 테스트 추가 (RED)**

```python
class TestButtons(unittest.TestCase):
    def test_callback_data_within_limit(self):
        rows = [{'name': 'a' * 24, 'unit': 'active', 'branch': 'main', 'last_activity': 0,
                 'worktrees': 0, 'trusted': True, 'enabled': True, 'dirty': False}]
        for row in lf.keyboard_for(rows)['inline_keyboard']:
            for btn in row:
                self.assertLessEqual(len(btn['callback_data'].encode()), 64)

    def test_running_project_gets_stop_button(self):
        rows = [{'name': 'a', 'unit': 'active', 'branch': 'main', 'last_activity': 0,
                 'worktrees': 0, 'trusted': True, 'enabled': True, 'dirty': False}]
        data = [b['callback_data'] for r in lf.keyboard_for(rows)['inline_keyboard'] for b in r]
        self.assertIn('down:a', data)
        self.assertNotIn('up:a', data)

    def test_callback_answers_before_running_ssm(self):
        order = []
        lf.tg_answer_callback = lambda cb: order.append('answer')
        lf.ssm_run = lambda script, timeout=25: order.append('ssm') or ENVELOPE_OK
        lf.lambda_handler(callback_event('up:app'), None)
        self.assertEqual(order[0], 'answer')

    def test_callback_from_other_user_is_ignored(self):
        lf.lambda_handler(callback_event('up:app', from_id=999), None)
        self.assertEqual(self.sent, [])

    def test_callback_with_bad_payload_is_ignored(self):
        lf.lambda_handler(callback_event('up:Bad Name'), None)
        self.assertEqual(self.ran, [])
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest discover -s tests -p test_lambda_function.py 2>&1 | tail -5`
Expected: `keyboard_for` 부재 ERROR.

- [ ] **Step 3: 구현**

```python
def tg_api(method, payload):
    token = os.environ['TELEGRAM_TOKEN']
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    urllib.request.urlopen(req, timeout=10)


def tg_send(chat_id, text, keyboard=None):
    for i in range(0, max(len(text), 1), TG_MAX):
        payload = {'chat_id': chat_id, 'text': text[i:i + TG_MAX] or ' '}
        if keyboard and i == 0:
            payload['reply_markup'] = keyboard
        tg_api('sendMessage', payload)


def tg_answer_callback(callback_id):
    """Telegram shows a spinner on the button until this is called, so it runs
    before the SSM round trip, not after."""
    try:
        tg_api('answerCallbackQuery', {'callback_query_id': callback_id})
    except Exception:  # noqa: BLE001 - a missing ack must not fail the command
        pass


def tg_edit_markup(chat_id, message_id, keyboard):
    try:
        tg_api('editMessageReplyMarkup', {'chat_id': chat_id, 'message_id': message_id,
                                          'reply_markup': keyboard})
    except Exception:  # noqa: BLE001 - the list message may be gone
        pass


def keyboard_for(rows):
    """One row of buttons per project. callback_data stays well under the
    64-byte limit because names are at most 24 characters."""
    keys = []
    for p in rows:
        if p['unit'] == 'active':
            first = {'text': f"⏹ {p['name']}", 'callback_data': f"down:{p['name']}"}
        else:
            first = {'text': f"▶ {p['name']}", 'callback_data': f"up:{p['name']}"}
        row = [first]
        if p['worktrees']:
            row.append({'text': '🌳', 'callback_data': f"trees:{p['name']}"})
        keys.append(row)
    return {'inline_keyboard': keys}


CALLBACK_VERBS = {'up': cmd_up, 'down': cmd_down, 'trees': cmd_trees}


def handle_callback(cb):
    tg_answer_callback(cb.get('id'))
    chat_id = str(((cb.get('message') or {}).get('chat') or {}).get('id', ''))
    from_id = str((cb.get('from') or {}).get('id', ''))
    allowed = os.environ['ALLOWED_CHAT_ID']
    if chat_id != allowed or from_id != allowed:
        return
    verb, _, name = (cb.get('data') or '').partition(':')
    handler = CALLBACK_VERBS.get(verb)
    if handler is None or validate_name(name):
        return
    handler(chat_id, name)
    message_id = (cb.get('message') or {}).get('message_id')
    if message_id and verb in ('up', 'down'):
        env = parse_pocket(ssm_run(pocket_script('list'), timeout=20))
        if env.get('ok'):
            tg_edit_markup(chat_id, message_id, keyboard_for(env['data']['projects']))
```

`lambda_handler`는 본문을 파싱한 뒤 `callback_query`가 있으면 `handle_callback`으로 보내고 바로 200을 돌려준다. 예외 처리는 기존 메시지 경로와 같게 감싼다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: 커밋**

```bash
git add src/lambda_function.py tests/test_lambda_function.py
git commit -m "[feat] 람다 v6 — 프로젝트 목록 인라인 버튼"
```

---

### Task 9: 이행 스크립트와 문서

**Files:**
- Create: `ec2/migrate-v6.sh`
- Rename: `iam/gamer4-operator-policy.json` → `iam/operator-policy.example.json`
- Modify: `README.md`, `docs/SETUP.md`, `docs/OPERATIONS.md`, `ec2-claude-md-patch.md`

**Interfaces:**
- Consumes: Task 2~8의 모든 파일
- Produces: 박스에서 한 번 돌리는 이행 스크립트와 v6 문서

- [ ] **Step 1: 이행 스크립트**

`ec2/migrate-v6.sh` (root로 실행, 멱등):

1. `/home/ubuntu/work` 생성
2. 인자로 받은 `<이름>=<경로>` 쌍마다 링크 생성(이미 있으면 그대로). 예: `migrate-v6.sh myapp=/home/ubuntu/myapp other=/home/ubuntu/workspace/other`
3. `~/.config/gamer4` → `~/.config/pocket-claude` 이동(있을 때만), `secrets.env`·`telegram.env` 권한 유지
4. 구 유닛 정리: `claude-rc@ops`가 enable돼 있으면 `disable --now`
5. cron의 `idle-watch.sh` 등록 제거
6. 각 프로젝트에 `pocket trust` 안내 출력(자동 실행하지 않는다 — 대화형이라 실패할 수 있다)
7. 마지막에 `pocket list`를 찍어 결과를 보여준다

- [ ] **Step 2: IAM 템플릿 일반화**

`iam/operator-policy.example.json`으로 옮기고 `gamer4-sync`→`STACK_NAME`, `gamer4-sync-*`→`FUNCTION_PREFIX-*`, `gamer4-notify-alerts`·`ec2-boot-manager`는 주석 대신 `EXTRA_FUNCTION_NAME` 예시로 바꾼다. 실제 값은 저장소에 두지 않는다.

- [ ] **Step 3: 문서 갱신**

- `README.md`: 구조도·명령표를 프로젝트 단위로. 예시 프로젝트 이름은 `myapp`
- `docs/SETUP.md`: 1회 설정을 "리포마다 링크 + `pocket trust`"로. 유휴 정지 절 삭제(수동 정지), 스모크 표에 `/projects` 버튼·`/trees`·`prune` 추가
- `docs/OPERATIONS.md`: 명령표 교체, 세션 종료 의미 유지, 유휴 감시 절을 "수동 정지" 절로 교체(되살리는 방법은 스펙 §5-8 링크), 서비스 관리에 slice·`pocket` 추가
- `ec2-claude-md-patch.md`: "OPERATIONS 갱신" 규칙을 프로젝트 문서 일반으로 바꾸고, 환경변수 출력 금지 문구 유지

- [ ] **Step 4: 전체 검증**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -2
bash tests/test_pocket.py 2>/dev/null || python3 -m unittest tests.test_pocket 2>&1 | tail -2
bash tests/test_claude_rc_wrap.sh | tail -1
bash tests/test_worktree_env_hook.sh | tail -1
bash tests/test_idle_watch.sh | tail -1
grep -rniE 'gamer4' README.md docs/SETUP.md docs/OPERATIONS.md ec2/ src/ iam/ ec2-claude-md-patch.md || echo "no project-specific names"
grep -rnE 'i-[0-9a-f]{17}|[0-9]{12}|AKIA|sk-ant-|github_pat_' . --exclude-dir=.git || echo "secrets scan clean"
```
Expected: 테스트 전부 통과, `no project-specific names`, `secrets scan clean`

- [ ] **Step 5: 커밋**

```bash
git add -A
git commit -m "[docs] v6 이행 스크립트와 문서 — 프로젝트 단위 구조로 정리"
```

---

## 배포 (계획 실행 후, 사용자와 함께)

1. 박스에 `ec2/*` 배포 → `sudo bash /tmp/install.sh`
2. `sudo bash /tmp/migrate-v6.sh myapp=/home/ubuntu/myapp …`
3. 프로젝트마다 `pocket trust <이름>` (실패하면 SSM 셸에서 직접 수락)
4. Lambda 재배포 + 봇 명령 메뉴 갱신(`setMyCommands`)
5. 스모크: `/projects` 버튼으로 켜고 끄기, 앱에서 새 세션 → 워크트리에 `.env`가 복사됐는지, `/trees`·정리, 재부팅 후 스티키 복원

## 범위 밖 (단계 2·3)

managed settings 게이트, `PocketCommand` SSM 문서와 Lambda 역할 축소, 샌드박스, `/add`(clone), 프로젝트 `.env`를 맥에서 전달하는 스크립트, 허브 세션, 포크용 SAM 템플릿.

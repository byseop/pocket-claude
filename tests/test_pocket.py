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
# start|enable|stop|disable fail when $FAKE_SYSTEMCTL_FAIL is set, to exercise
# the pocket up/down failure path.
verb=$1; unit=$2
case "$verb" in
  start|enable|stop|disable)
    [ -n "${FAKE_SYSTEMCTL_FAIL:-}" ] && { echo "$@" >> "$FAKE_STATE_DIR/systemctl.log"; exit 1; }
    ;;
esac
f="$FAKE_STATE_DIR/$unit.$verb"
if [ -f "$f" ]; then cat "$f"; else echo unknown; fi
echo "$@" >> "$FAKE_STATE_DIR/systemctl.log"
"""

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
        (self.state / f'claude-rc@{name}.service.is-active').write_text(unit + '\n')
        (self.state / f'claude-rc@{name}.service.is-enabled').write_text(
            ('enabled' if enabled else 'disabled') + '\n')
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
        (self.state / 'claude-rc@myapp.service.is-active').write_text('inactive\n')
        (self.state / 'claude-rc@myapp.service.is-enabled').write_text('disabled\n')
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
        data = self.json_of(self.run_pocket('list', '--json', FAKE_BRANCH='feat/x', FAKE_DIRTY=' M a.py\n'))
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

    def test_up_reports_systemctl_failure(self):
        # Review fix: a failed enable/start must surface an error, not a silent ok.
        self.make_project('app', trusted=True)
        data = self.json_of(self.run_pocket('up', 'app', '--json', FAKE_SYSTEMCTL_FAIL='1'))
        self.assertFalse(data['ok'])
        self.assertIn('실패', data['error'])

    def test_down_disables_and_stops(self):
        self.make_project('app', trusted=True, unit='active', enabled=True)
        data = self.json_of(self.run_pocket('down', 'app', '--json'))
        self.assertTrue(data['ok'])
        log = (self.state / 'systemctl.log').read_text()
        self.assertIn('disable claude-rc@app.service', log)
        self.assertIn('stop claude-rc@app.service', log)

    def test_down_unknown_project_is_error(self):
        self.assertFalse(self.json_of(self.run_pocket('down', 'nope', '--json'))['ok'])

    def test_down_reports_systemctl_failure(self):
        # Review fix: a failed disable/stop must surface an error, not a silent ok.
        self.make_project('app', trusted=True, unit='active', enabled=True)
        data = self.json_of(self.run_pocket('down', 'app', '--json', FAKE_SYSTEMCTL_FAIL='1'))
        self.assertFalse(data['ok'])
        self.assertIn('실패', data['error'])


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
        (self.home / '.claude' / '.env').write_text('export CLAUDE_CODE_OAUTH_TOKEN=x\n')
        d = self.json_of(self.run_pocket('status', '--json'))['data']
        self.assertTrue(d['auth']['token_in_env'])

    def test_status_reports_missing_credentials(self):
        d = self.json_of(self.run_pocket('status', '--json'))['data']
        self.assertFalse(d['auth']['creds'])


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


if __name__ == '__main__':
    unittest.main()

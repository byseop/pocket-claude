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


if __name__ == '__main__':
    unittest.main()

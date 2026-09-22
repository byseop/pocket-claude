"""Unit tests for the pure helpers. No AWS credentials required."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import lambda_function as lf


class TestStripAnsi(unittest.TestCase):
    def test_removes_color_codes(self):
        self.assertEqual(lf.strip_ansi('\x1b[31mred\x1b[0m'), 'red')

    def test_removes_cursor_movement(self):
        self.assertEqual(lf.strip_ansi('a\x1b[2Kb'), 'ab')

    def test_leaves_plain_text_untouched(self):
        self.assertEqual(lf.strip_ansi('hello world'), 'hello world')

    def test_preserves_newlines(self):
        self.assertEqual(lf.strip_ansi('\x1b[1ma\nb'), 'a\nb')


class TestDetectAuthError(unittest.TestCase):
    def test_detects_revoked_token(self):
        pane = 'some output\n Please run /login  API Error: 401 OAuth access token has been revoked.\n'
        self.assertIn('401', lf.detect_auth_error(pane))

    def test_returns_none_when_healthy(self):
        pane = 'Claude Code v2.1.220\n bypass permissions on\n'
        self.assertIsNone(lf.detect_auth_error(pane))

    def test_returns_last_match_when_several(self):
        pane = 'API Error: 401 first\nnormal line\nPlease run /login second\n'
        self.assertIn('second', lf.detect_auth_error(pane))

    def test_handles_empty_input(self):
        self.assertIsNone(lf.detect_auth_error(''))


class TestFormatUptime(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    def test_minutes_only(self):
        launched = self.now - timedelta(minutes=13)
        self.assertEqual(lf.format_uptime(launched, self.now), '13m')

    def test_hours_and_minutes(self):
        launched = self.now - timedelta(hours=2, minutes=13)
        self.assertEqual(lf.format_uptime(launched, self.now), '2h 13m')

    def test_just_started(self):
        self.assertEqual(lf.format_uptime(self.now, self.now), '0m')


class TestBuildStatus(unittest.TestCase):
    def test_all_healthy(self):
        out = lf.build_status('running', '2h 13m', [('ops', 'active')], None)
        self.assertIn('✅ EC2 running (2h 13m)', out)
        self.assertIn('🟢 claude-rc@ops active', out)
        self.assertIn('✅ 인증 OK', out)
        self.assertNotIn('❌', out)

    def test_auth_failure_includes_recovery_steps(self):
        out = lf.build_status('running', '5m', [('ops', 'active')], 'API Error: 401 revoked')
        self.assertIn('❌', out)
        self.assertIn('claude auth login', out)
        self.assertIn('401', out)
        self.assertNotIn('setup-token', out)

    def test_ops_unit_down(self):
        out = lf.build_status('running', '5m', [('ops', 'inactive')], None)
        self.assertIn('❌ claude-rc@ops', out)

    def test_ops_unit_missing_is_reported_down(self):
        out = lf.build_status('running', '5m', [], None)
        self.assertIn('❌ claude-rc@ops', out)

    def test_extra_units_are_listed(self):
        out = lf.build_status('running', '5m', [('ops', 'active'), ('t1', 'activating')], None)
        self.assertIn('claude-rc@t1 activating', out)

    def test_stopped_instance_skips_service_lines(self):
        out = lf.build_status('stopped', None, None, None)
        self.assertIn('EC2 stopped', out)
        self.assertIn('/start', out)
        self.assertNotIn('claude-rc@', out)


class TestStatusParsing(unittest.TestCase):
    def test_parse_units_extracts_name_and_state(self):
        text = 'claude-rc@ops.service active\nclaude-rc@t1.service inactive\n'
        self.assertEqual(lf.parse_units(text), [('ops', 'active'), ('t1', 'inactive')])

    def test_parse_units_ignores_noise(self):
        self.assertEqual(lf.parse_units('\n0 loaded units listed.\n'), [])

    def test_auth_problem_flags_token_left_in_env(self):
        msg = lf.auth_problem('', 'TOKEN_IN_ENV\nCREDS_OK')
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN', msg)

    def test_auth_problem_flags_missing_login(self):
        msg = lf.auth_problem('', 'CREDS_MISSING')
        self.assertIn('claude auth login', msg)

    def test_auth_problem_flags_401_on_screen(self):
        msg = lf.auth_problem('x\nAPI Error: 401 revoked\n', 'CREDS_OK')
        self.assertIn('401', msg)

    def test_auth_problem_none_when_healthy(self):
        self.assertIsNone(lf.auth_problem('Claude Code v2.1.278\n', 'CREDS_OK'))

    def test_status_script_reads_units_pane_and_flags(self):
        s = lf.status_script()
        self.assertIn("list-units 'claude-rc@*'", s)
        self.assertIn('tmux -L rc-ops capture-pane -t rc-ops', s)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN', s)
        self.assertIn('.credentials.json', s)
        self.assertEqual(s.count('echo ===RC==='), 2)


class TestSessions(unittest.TestCase):
    NOW = 1_800_000_000

    def test_format_age(self):
        self.assertEqual(lf.format_age(self.NOW - 30, self.NOW), '방금')
        self.assertEqual(lf.format_age(self.NOW - 5 * 60, self.NOW), '5분 전')
        self.assertEqual(lf.format_age(self.NOW - 3 * 3600, self.NOW), '3시간 전')
        self.assertEqual(lf.format_age(self.NOW - 2 * 86400, self.NOW), '2일 전')

    def test_parse_sessions(self):
        text = f'ops active main {self.NOW - 60}\nt1 inactive t1 0\n'
        self.assertEqual(
            lf.parse_sessions(text),
            [('ops', 'active', 'main', self.NOW - 60), ('t1', 'inactive', 't1', 0)],
        )

    def test_parse_sessions_skips_malformed_lines(self):
        self.assertEqual(lf.parse_sessions('garbage\n'), [])

    def test_build_sessions_marks_active_and_age(self):
        rows = [('ops', 'active', 'main', self.NOW - 300), ('t1', 'inactive', 't1', 0)]
        out = lf.build_sessions(rows, self.NOW)
        self.assertIn('🟢 gamer4-ops', out)
        self.assertIn('main', out)
        self.assertIn('5분 전', out)
        self.assertIn('⚪ gamer4-t1', out)

    def test_build_sessions_empty(self):
        self.assertIn('없', lf.build_sessions([], self.NOW))

    def test_sessions_script_walks_ops_and_worktrees(self):
        s = lf.sessions_script()
        self.assertIn('sudo -u ubuntu', s)
        self.assertIn('/home/ubuntu/worktrees', s)
        self.assertIn('systemctl is-active', s)
        self.assertIn('rev-parse --abbrev-ref HEAD', s)
        self.assertIn('/home/ubuntu/.claude/projects', s)
        self.assertNotIn('[[', s)


class TestFormatError(unittest.TestCase):
    def test_includes_command_and_exception_text(self):
        out = lf.format_error('/status', RuntimeError('boom'))
        self.assertIn('/status', out)
        self.assertIn('boom', out)
        self.assertIn('RuntimeError', out)

    def test_truncates_very_long_messages(self):
        out = lf.format_error('/view', RuntimeError('x' * 5000))
        self.assertLessEqual(len(out), 1200)

    def test_handles_exception_with_empty_message(self):
        out = lf.format_error('/stop', ValueError())
        self.assertIn('ValueError', out)


class TestParseCommand(unittest.TestCase):
    def test_splits_command_and_argument(self):
        self.assertEqual(lf.parse_command('/new feat-x'), ('/new', 'feat-x'))

    def test_strips_bot_suffix_from_command_only(self):
        self.assertEqual(lf.parse_command('/new@pocket_bot feat-x'), ('/new', 'feat-x'))

    def test_no_argument_gives_empty_string(self):
        self.assertEqual(lf.parse_command('/status'), ('/status', ''))

    def test_collapses_surrounding_whitespace(self):
        self.assertEqual(lf.parse_command('  /kill   t1  '), ('/kill', 't1'))


class TestValidateName(unittest.TestCase):
    def test_accepts_lowercase_digits_and_hyphen(self):
        self.assertIsNone(lf.validate_name('feat-x2'))

    def test_rejects_empty(self):
        self.assertIsNotNone(lf.validate_name(''))

    def test_rejects_uppercase(self):
        self.assertIsNotNone(lf.validate_name('Feat'))

    def test_rejects_shell_metacharacters(self):
        for bad in ('a;b', 'a b', 'a/b', '$(x)', '../x', 'a`b'):
            self.assertIsNotNone(lf.validate_name(bad), bad)

    def test_rejects_over_24_chars(self):
        self.assertIsNone(lf.validate_name('a' * 24))
        self.assertIsNotNone(lf.validate_name('a' * 25))

    def test_reserves_ops(self):
        self.assertIn('ops', lf.validate_name('ops'))

    def test_rejects_trailing_newline(self):
        self.assertIsNotNone(lf.validate_name('t1\n'))
        self.assertIsNotNone(lf.validate_name('t1\nrm -rf /'))


class TestSessionScripts(unittest.TestCase):
    def test_workdir_ops_is_main_checkout(self):
        self.assertEqual(lf.workdir('ops'), '/home/ubuntu/gamer4info')

    def test_workdir_other_is_worktree(self):
        self.assertEqual(lf.workdir('t1'), '/home/ubuntu/worktrees/t1')

    def test_as_ubuntu_wraps_in_heredoc(self):
        out = lf.as_ubuntu('echo hi')
        self.assertTrue(out.startswith("sudo -u ubuntu -H sh <<'EOF'\n"))
        self.assertIn('\necho hi\n', out)
        self.assertTrue(out.rstrip('\n').endswith('EOF'))

    def test_new_creates_worktree_copies_env_and_starts_unit(self):
        s = lf.new_session_script('t1')
        self.assertIn('sudo -u ubuntu', s)
        self.assertIn('git -C /home/ubuntu/gamer4info worktree add /home/ubuntu/worktrees/t1 -b t1 origin/main', s)
        self.assertIn('cp /home/ubuntu/gamer4info/.env /home/ubuntu/worktrees/t1/.env', s)
        self.assertIn('sudo systemctl start claude-rc@t1', s)
        self.assertTrue(s.rstrip('\n').endswith('EOF'))

    def test_new_reuses_existing_branch(self):
        s = lf.new_session_script('t1')
        self.assertIn('show-ref --verify --quiet refs/heads/t1', s)
        self.assertIn('worktree add /home/ubuntu/worktrees/t1 t1\n', s)

    def test_new_uses_no_bash_only_syntax(self):
        s = lf.new_session_script('t1')
        self.assertNotIn('[[', s)
        self.assertNotIn('<(', s)

    def test_kill_stops_unit_and_keeps_worktree(self):
        s = lf.kill_session_script('t1')
        self.assertIn('sudo systemctl stop claude-rc@t1', s)
        self.assertNotIn('worktree remove', s)
        self.assertIn('|| true', s.splitlines()[-2])

    def test_rm_refuses_dirty_worktree_before_removing(self):
        s = lf.rm_session_script('t1')
        self.assertIn('sudo systemctl stop claude-rc@t1', s)
        self.assertIn('git -C /home/ubuntu/worktrees/t1 status --porcelain', s)
        self.assertIn('echo DIRTY', s)
        self.assertIn('git -C /home/ubuntu/gamer4info worktree remove /home/ubuntu/worktrees/t1', s)
        self.assertIn('echo REMOVED', s)
        self.assertLess(s.index('echo DIRTY'), s.index('worktree remove'))
        self.assertNotIn('--force', s)
        self.assertIn('echo RM_FAILED', s)
        self.assertIn('echo MISSING', s)
        self.assertLess(s.index('worktree remove'), s.index('echo RM_FAILED'))
        self.assertLess(s.index('echo RM_FAILED'), s.index('echo REMOVED'))


class TestHandlerRouting(unittest.TestCase):
    """The handler must never raise: a crash means Telegram shows nothing."""

    def setUp(self):
        self.sent = []
        self.ran = []
        self._chat = os.environ.get('ALLOWED_CHAT_ID')
        self._tg = lf.tg_send
        self._ssm = lf.ssm_run
        self._ready = lf.ssm_ready
        self._describe = lf.describe
        self._id = lf.INSTANCE_ID
        self._ec2 = lf.ec2
        lf.tg_send = lambda chat_id, text: self.sent.append((chat_id, text))
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'active'
        lf.ssm_ready = lambda: True
        lf.describe = lambda: ('running', None)
        lf.INSTANCE_ID = 'i-test'
        os.environ['ALLOWED_CHAT_ID'] = '111'

    def tearDown(self):
        lf.tg_send = self._tg
        lf.ssm_run = self._ssm
        lf.ssm_ready = self._ready
        lf.describe = self._describe
        lf.INSTANCE_ID = self._id
        lf.ec2 = self._ec2
        if self._chat is None:
            os.environ.pop('ALLOWED_CHAT_ID', None)
        else:
            os.environ['ALLOWED_CHAT_ID'] = self._chat

    @staticmethod
    def _event(text, chat_id=111):
        import json as _j
        return {'body': _j.dumps({'message': {'chat': {'id': chat_id}, 'text': text}})}

    def test_rejects_other_chat_ids_without_replying(self):
        res = lf.lambda_handler(self._event('/status', chat_id=999), None)
        self.assertEqual(res['statusCode'], 200)
        self.assertEqual(self.sent, [])

    def test_unknown_command_gets_help(self):
        lf.lambda_handler(self._event('/nope'), None)
        self.assertIn('/start', self.sent[0][1])
        self.assertIn('/new', self.sent[0][1])

    def test_plain_text_is_ignored(self):
        lf.lambda_handler(self._event('안녕'), None)
        self.assertEqual(self.sent, [])

    def test_command_failure_is_reported_not_raised(self):
        def boom(chat_id, arg):
            raise RuntimeError('describe failed')

        lf.HANDLERS['/status'] = boom
        try:
            res = lf.lambda_handler(self._event('/status'), None)
        finally:
            lf.HANDLERS['/status'] = lf.cmd_status
        self.assertEqual(res['statusCode'], 200)
        self.assertTrue(self.sent, 'failure must be reported to Telegram')
        self.assertIn('describe failed', self.sent[0][1])

    def test_reports_even_when_body_is_malformed(self):
        res = lf.lambda_handler({'body': 'not json'}, None)
        self.assertEqual(res['statusCode'], 200)

    def test_new_with_bad_name_replies_without_ssm(self):
        lf.lambda_handler(self._event('/new Bad;Name'), None)
        self.assertEqual(self.ran, [])
        self.assertTrue(self.sent)

    def test_new_without_name_replies_usage(self):
        lf.lambda_handler(self._event('/new'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('/new', self.sent[0][1])

    def test_new_runs_script_and_names_session(self):
        lf.lambda_handler(self._event('/new t1'), None)
        self.assertEqual(len(self.ran), 1)
        self.assertIn('claude-rc@t1', self.ran[0])
        self.assertIn('gamer4-t1', self.sent[0][1])

    def test_new_when_instance_stopped_does_not_run_ssm(self):
        lf.describe = lambda: ('stopped', None)
        lf.lambda_handler(self._event('/new t1'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('/start', self.sent[0][1])

    def test_kill_ops_is_refused(self):
        lf.lambda_handler(self._event('/kill ops'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('ops', self.sent[0][1])

    def test_kill_runs_stop_script(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'inactive'
        lf.lambda_handler(self._event('/kill t1'), None)
        self.assertIn('systemctl stop claude-rc@t1', self.ran[0])
        self.assertIn('gamer4-t1', self.sent[0][1])

    def test_rm_ops_is_refused(self):
        lf.lambda_handler(self._event('/rm ops'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('ops', self.sent[0][1])

    def test_rm_dirty_reports_refusal(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'DIRTY\n'
        lf.lambda_handler(self._event('/rm t1'), None)
        self.assertIn('커밋', self.sent[0][1])

    def test_rm_removed_reports_success(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'REMOVED\n'
        lf.lambda_handler(self._event('/rm t1'), None)
        self.assertIn('삭제', self.sent[0][1])

    def test_rm_failed_reports_failure(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'RM_FAILED\n'
        lf.lambda_handler(self._event('/rm t1'), None)
        self.assertIn('실패', self.sent[0][1])

    def test_rm_missing_dir_reports_nothing_to_remove(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'MISSING\n'
        lf.lambda_handler(self._event('/rm t1'), None)
        self.assertIn('없', self.sent[0][1])

    def test_view_is_gone(self):
        lf.lambda_handler(self._event('/view'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('/sessions', self.sent[0][1])   # falls through to HELP

    def test_status_reports_units_and_auth(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or (
            'claude-rc@ops.service active\n===RC===\nClaude Code\n===RC===\nCREDS_OK\n'
        )
        lf.describe = lambda: ('running', datetime.now(timezone.utc))
        lf.lambda_handler(self._event('/status'), None)
        self.assertIn('🟢 claude-rc@ops active', self.sent[0][1])
        self.assertIn('✅ 인증 OK', self.sent[0][1])

    def test_status_reports_incomplete_ssm_output(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or ''
        lf.describe = lambda: ('running', datetime.now(timezone.utc))
        lf.lambda_handler(self._event('/status'), None)
        self.assertIn('SSM', self.sent[0][1])
        self.assertNotIn('인증 OK', self.sent[0][1])

    def test_new_reports_failure_when_unit_not_active(self):
        lf.ssm_run = lambda script, timeout=25: (
            self.ran.append(script) or 'fatal: not a git repository\n'
        )
        lf.lambda_handler(self._event('/new t1'), None)
        self.assertIn('실패', self.sent[0][1])

    def test_sessions_lists_rows(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'ops active main 0\n'
        lf.lambda_handler(self._event('/sessions'), None)
        self.assertIn('gamer4-ops', self.sent[0][1])

    def test_start_mentions_ops_session(self):
        started = []
        lf.describe = lambda: ('stopped', None)
        lf.ec2 = type('E', (), {'start_instances': lambda self, **kw: started.append(kw)})()
        lf.lambda_handler(self._event('/start'), None)
        self.assertTrue(started)
        self.assertIn('gamer4-ops', self.sent[0][1])


if __name__ == '__main__':
    unittest.main()

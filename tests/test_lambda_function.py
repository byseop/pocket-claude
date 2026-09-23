"""Unit tests for the pure helpers. No AWS credentials required."""

import json as _j
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import lambda_function as lf


def envelope_text(ok, data=None, error=None):
    """A mixed-stdout string carrying one pocket envelope, as SSM would return it."""
    body = _j.dumps({'ok': ok, 'data': data or {}, 'error': error})
    return f'{lf.BEGIN}\n{body}\n{lf.END}\n'


# A ready-made "everything worked" envelope for callback tests: it has to
# satisfy both cmd_up/cmd_down's success formatter (name, unit) and the
# post-command list refresh (projects) with a single stubbed ssm_run.
ENVELOPE_OK = envelope_text(True, {'name': 'app', 'unit': 'active', 'projects': []})


def callback_event(data, from_id=111, chat_id=111, message_id=1, callback_id='cb1'):
    """A Telegram update body carrying a callback_query (inline button press)."""
    return {'body': _j.dumps({
        'callback_query': {
            'id': callback_id,
            'from': {'id': from_id},
            'message': {'chat': {'id': chat_id}, 'message_id': message_id},
            'data': data,
        }
    })}


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


class TestFormatAge(unittest.TestCase):
    NOW = 1_800_000_000

    def test_format_age(self):
        self.assertEqual(lf.format_age(self.NOW - 30, self.NOW), '방금')
        self.assertEqual(lf.format_age(self.NOW - 5 * 60, self.NOW), '5분 전')
        self.assertEqual(lf.format_age(self.NOW - 3 * 3600, self.NOW), '3시간 전')
        self.assertEqual(lf.format_age(self.NOW - 2 * 86400, self.NOW), '2일 전')


class TestFormatError(unittest.TestCase):
    def test_includes_command_and_exception_text(self):
        out = lf.format_error('/status', RuntimeError('boom'))
        self.assertIn('/status', out)
        self.assertIn('boom', out)
        self.assertIn('RuntimeError', out)

    def test_truncates_very_long_messages(self):
        out = lf.format_error('/up', RuntimeError('x' * 5000))
        self.assertLessEqual(len(out), 1200)

    def test_handles_exception_with_empty_message(self):
        out = lf.format_error('/stop', ValueError())
        self.assertIn('ValueError', out)


class TestParseCommand(unittest.TestCase):
    def test_splits_command_and_argument(self):
        self.assertEqual(lf.parse_command('/up feat-x'), ('/up', 'feat-x'))

    def test_strips_bot_suffix_from_command_only(self):
        self.assertEqual(lf.parse_command('/up@pocket_bot feat-x'), ('/up', 'feat-x'))

    def test_no_argument_gives_empty_string(self):
        self.assertEqual(lf.parse_command('/status'), ('/status', ''))

    def test_collapses_surrounding_whitespace(self):
        self.assertEqual(lf.parse_command('  /down   t1  '), ('/down', 't1'))


class TestValidateName(unittest.TestCase):
    def test_accepts_lowercase_digits_and_hyphen(self):
        self.assertIsNone(lf.validate_name('feat-x2'))

    def test_rejects_empty(self):
        self.assertIsNotNone(lf.validate_name(''))

    def test_rejects_uppercase(self):
        self.assertIsNotNone(lf.validate_name('Feat'))

    def test_rejects_leading_hyphen(self):
        self.assertIsNotNone(lf.validate_name('-abc'))

    def test_rejects_shell_metacharacters(self):
        for bad in ('a;b', 'a b', 'a/b', '$(x)', '../x', 'a`b'):
            self.assertIsNotNone(lf.validate_name(bad), bad)

    def test_rejects_over_24_chars(self):
        self.assertIsNone(lf.validate_name('a' * 24))
        self.assertIsNotNone(lf.validate_name('a' * 25))

    def test_does_not_reserve_ops(self):
        self.assertIsNone(lf.validate_name('ops'))

    def test_rejects_trailing_newline(self):
        self.assertIsNotNone(lf.validate_name('t1\n'))
        self.assertIsNotNone(lf.validate_name('t1\nrm -rf /'))


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
        body = '{"ok": true, "verb": "list", "data": {"projects": []}}'
        out = f'{lf.BEGIN}\n{body}\n{lf.END}\n' + 'x' * lf.SSM_STDOUT_LIMIT
        r = lf.parse_pocket(out)
        self.assertFalse(r['ok'])
        self.assertIn('잘렸', r['error'])

    def test_parse_reports_missing_sentinels(self):
        r = lf.parse_pocket('boom: command not found')
        self.assertFalse(r['ok'])
        self.assertIn('응답', r['error'])

    def test_format_projects_marks_running(self):
        rows = [{'name': 'a', 'unit': 'active', 'branch': 'main', 'last_activity': 0,
                 'worktrees': 2, 'trusted': True, 'enabled': True, 'dirty': False},
                {'name': 'b', 'unit': 'inactive', 'branch': 'main', 'last_activity': 0,
                 'worktrees': 0, 'trusted': False, 'enabled': False, 'dirty': False}]
        out = lf.format_projects(rows)
        self.assertIn('🟢 a', out)
        self.assertIn('⚪ b', out)
        self.assertIn('신뢰 필요', out)      # b is untrusted

    def test_format_projects_empty(self):
        self.assertIn('프로젝트가 없어요', lf.format_projects([]))

    def test_format_status_reports_memory_disk_and_auth(self):
        data = {
            'mem': {'available_mb': 512, 'total_mb': 1024},
            'disk': {'free_gb': 12.3},
            'auth': {'creds': True, 'token_in_env': False},
            'servers': [{'name': 'a', 'unit': 'active'}],
            'projects': 3,
            'max_servers': 2,
        }
        out = lf.format_status(data, '2h 13m')
        self.assertIn('running (2h 13m)', out)
        self.assertIn('🟢 a active', out)
        self.assertIn('프로젝트 3개', out)
        self.assertIn('✅ 인증 OK', out)

    def test_format_status_flags_token_in_env(self):
        data = {
            'mem': {'available_mb': 1, 'total_mb': 1}, 'disk': {'free_gb': 1},
            'auth': {'creds': True, 'token_in_env': True}, 'servers': [],
            'projects': 0, 'max_servers': 2,
        }
        out = lf.format_status(data, '1m')
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN', out)
        # Recovery steps for a leftover token don't need a fresh login: the
        # concrete restart command is what proves the block was appended, not
        # just the problem line (which already mentions ".env").
        self.assertIn('복구', out)
        self.assertIn('systemctl restart claude-rc@', out)
        self.assertNotIn('claude auth login', out)

    def test_format_status_flags_missing_creds(self):
        data = {
            'mem': {'available_mb': 1, 'total_mb': 1}, 'disk': {'free_gb': 1},
            'auth': {'creds': False, 'token_in_env': False}, 'servers': [],
            'projects': 0, 'max_servers': 2,
        }
        out = lf.format_status(data, '1m')
        # The problem line alone already says "claude auth login"; the real
        # regression check is that the concrete recovery block follows it.
        self.assertIn('복구', out)
        self.assertIn('claude auth login', out)
        self.assertIn('pocket up', out)

    def test_format_trees_lists_branches_and_marks(self):
        data = {'name': 'a', 'trees': [
            {'branch': 't1', 'dirty': True, 'unpushed': False, 'locked': False, 'in_use': False, 'path': '/x'},
            {'branch': 't2', 'dirty': False, 'unpushed': False, 'locked': False, 'in_use': False, 'path': '/y'},
        ]}
        out = lf.format_trees(data)
        self.assertIn('t1', out)
        self.assertIn('미커밋', out)
        self.assertIn('정리 가능', out)

    def test_format_trees_empty(self):
        out = lf.format_trees({'name': 'a', 'trees': []})
        self.assertIn('a', out)
        self.assertIn('없어요', out)


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
        lf.tg_send = lambda chat_id, text, keyboard=None: self.sent.append((chat_id, text))
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or envelope_text(True, {})
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
        return {'body': _j.dumps({'message': {'chat': {'id': chat_id}, 'text': text}})}

    def test_rejects_other_chat_ids_without_replying(self):
        res = lf.lambda_handler(self._event('/status', chat_id=999), None)
        self.assertEqual(res['statusCode'], 200)
        self.assertEqual(self.sent, [])

    def test_unknown_command_gets_help(self):
        lf.lambda_handler(self._event('/nope'), None)
        self.assertIn('/start', self.sent[0][1])
        self.assertIn('/up', self.sent[0][1])

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

    def test_removed_commands_fall_through_to_help(self):
        for text in ('/new t1', '/kill t1', '/rm t1', '/sessions', '/view'):
            self.sent.clear()
            self.ran.clear()
            lf.lambda_handler(self._event(text), None)
            self.assertEqual(self.ran, [], text)
            self.assertIn('/up', self.sent[0][1], text)

    def test_projects_lists_rows(self):
        rows = [{'name': 'a', 'unit': 'active', 'branch': 'main', 'last_activity': 0,
                 'worktrees': 0, 'trusted': True, 'enabled': True, 'dirty': False}]
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or envelope_text(True, {'projects': rows})
        lf.lambda_handler(self._event('/projects'), None)
        self.assertIn('pocket list --json', self.ran[0])
        self.assertIn('🟢 a', self.sent[0][1])

    def test_projects_reports_error_envelope(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or envelope_text(False, error='박스 문제')
        lf.lambda_handler(self._event('/projects'), None)
        self.assertIn('⚠️', self.sent[0][1])
        self.assertIn('박스 문제', self.sent[0][1])

    def test_projects_when_ssm_not_ready_does_not_call_ssm(self):
        lf.ssm_ready = lambda: False
        lf.lambda_handler(self._event('/projects'), None)
        self.assertEqual(self.ran, [])
        self.assertTrue(self.sent)

    def test_up_with_bad_name_replies_without_ssm(self):
        lf.lambda_handler(self._event('/up Bad;Name'), None)
        self.assertEqual(self.ran, [])
        self.assertTrue(self.sent)

    def test_up_runs_pocket_verb_and_reports_success(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or envelope_text(True, {'name': 't1', 'unit': 'active'})
        lf.lambda_handler(self._event('/up t1'), None)
        self.assertIn('pocket up t1 --json', self.ran[0])
        self.assertIn('t1', self.sent[0][1])

    def test_up_reports_error_envelope(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or envelope_text(False, error='신뢰 필요')
        lf.lambda_handler(self._event('/up t1'), None)
        self.assertIn('⚠️', self.sent[0][1])
        self.assertIn('신뢰 필요', self.sent[0][1])

    def test_up_when_instance_stopped_does_not_call_ssm(self):
        lf.describe = lambda: ('stopped', None)
        lf.lambda_handler(self._event('/up myapp'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('/start', self.sent[0][1])

    def test_down_runs_pocket_verb_and_reports_success(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or envelope_text(True, {'name': 't1', 'unit': 'inactive'})
        lf.lambda_handler(self._event('/down t1'), None)
        self.assertIn('pocket down t1 --json', self.ran[0])
        self.assertIn('t1', self.sent[0][1])

    def test_trees_runs_pocket_verb_and_reports_success(self):
        data = {'name': 't1', 'trees': []}
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or envelope_text(True, data)
        lf.lambda_handler(self._event('/trees t1'), None)
        self.assertIn('pocket trees t1 --json', self.ran[0])
        self.assertIn('t1', self.sent[0][1])

    def test_trees_with_bad_name_replies_without_ssm(self):
        lf.lambda_handler(self._event('/trees Bad;Name'), None)
        self.assertEqual(self.ran, [])
        self.assertTrue(self.sent)

    def test_status_reports_envelope(self):
        data = {
            'mem': {'available_mb': 500, 'total_mb': 1000},
            'disk': {'free_gb': 10},
            'auth': {'creds': True, 'token_in_env': False},
            'servers': [],
            'projects': 2,
            'max_servers': 2,
        }
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or envelope_text(True, data)
        lf.describe = lambda: ('running', datetime.now(timezone.utc))
        lf.lambda_handler(self._event('/status'), None)
        self.assertIn('✅ EC2 running', self.sent[0][1])
        self.assertIn('인증 OK', self.sent[0][1])

    def test_status_reports_incomplete_ssm_output(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or ''
        lf.describe = lambda: ('running', datetime.now(timezone.utc))
        lf.lambda_handler(self._event('/status'), None)
        self.assertIn('⚠️', self.sent[0][1])

    def test_status_when_stopped_skips_ssm(self):
        lf.describe = lambda: ('stopped', None)
        lf.lambda_handler(self._event('/status'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('/start', self.sent[0][1])

    def test_start_mentions_status_not_a_removed_session(self):
        started = []
        lf.describe = lambda: ('stopped', None)
        lf.ec2 = type('E', (), {'start_instances': lambda self, **kw: started.append(kw)})()
        lf.lambda_handler(self._event('/start'), None)
        self.assertTrue(started)
        self.assertIn('/status', self.sent[0][1])
        self.assertNotIn('gamer4', self.sent[0][1])


class TestTgSend(unittest.TestCase):
    """tg_send itself, unmocked — TestHandlerRouting.setUp replaces tg_send
    wholesale, so this exercises the real chunking/keyboard-placement logic
    against a stubbed tg_api instead."""

    def test_send_attaches_keyboard_to_last_chunk(self):
        payloads = []
        orig_api = lf.tg_api
        lf.tg_api = lambda method, payload: payloads.append(payload)
        try:
            text = 'x' * (lf.TG_MAX + 10)
            lf.tg_send('111', text, keyboard={'inline_keyboard': []})
        finally:
            lf.tg_api = orig_api
        self.assertEqual(len(payloads), 2)
        self.assertNotIn('reply_markup', payloads[0])
        self.assertIn('reply_markup', payloads[-1])


class TestButtons(TestHandlerRouting):
    """Inline-button keyboard building and the callback_query handler.

    Reuses TestHandlerRouting's setUp/tearDown so describe/ssm_ready/
    INSTANCE_ID/ALLOWED_CHAT_ID are stubbed the same way as the message path.
    """

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

    def test_callback_skips_refresh_when_budget_spent(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or ENVELOPE_OK
        orig_budget = lf.CALLBACK_BUDGET
        lf.CALLBACK_BUDGET = 0
        try:
            lf.lambda_handler(callback_event('up:app'), None)
        finally:
            lf.CALLBACK_BUDGET = orig_budget
        self.assertEqual(len(self.ran), 1)

    def test_callback_refreshes_markup_when_time_allows(self):
        calls = []
        orig_edit = lf.tg_edit_markup
        lf.tg_edit_markup = lambda chat_id, message_id, keyboard: calls.append(keyboard)
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or ENVELOPE_OK
        try:
            lf.lambda_handler(callback_event('up:app'), None)
        finally:
            lf.tg_edit_markup = orig_edit
        self.assertEqual(len(self.ran), 2)
        self.assertEqual(len(calls), 1)

    def test_callback_failure_is_reported(self):
        def boom(chat_id, name):
            raise RuntimeError('버튼 처리 실패 테스트')

        orig_handler = lf.CALLBACK_VERBS['up']
        lf.CALLBACK_VERBS['up'] = boom
        try:
            res = lf.lambda_handler(callback_event('up:app'), None)
        finally:
            lf.CALLBACK_VERBS['up'] = orig_handler
        self.assertEqual(res['statusCode'], 200)
        self.assertTrue(self.sent)
        self.assertIn('버튼 처리 실패 테스트', self.sent[-1][1])


if __name__ == '__main__':
    unittest.main()

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
        out = lf.build_status('running', '2h 13m', True, None)
        self.assertIn('✅ EC2 running (2h 13m)', out)
        self.assertIn('✅ 인증 OK', out)
        self.assertNotIn('❌', out)

    def test_auth_failure_includes_recovery_steps(self):
        out = lf.build_status('running', '5m', True, 'API Error: 401 revoked')
        self.assertIn('❌', out)
        self.assertIn('claude setup-token', out)
        self.assertIn('401', out)

    def test_service_down(self):
        out = lf.build_status('running', '5m', False, None)
        self.assertIn('❌ claude-telegram.service', out)

    def test_stopped_instance_skips_service_lines(self):
        out = lf.build_status('stopped', None, None, None)
        self.assertIn('EC2 stopped', out)
        self.assertIn('/start', out)
        self.assertNotIn('claude-telegram.service', out)


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


class TestHandlerRouting(unittest.TestCase):
    """The handler must never raise: a crash means Telegram shows nothing."""

    def setUp(self):
        self.sent = []
        self._tg = lf.tg_send
        self._id = lf.INSTANCE_ID
        lf.tg_send = lambda chat_id, text: self.sent.append((chat_id, text))
        lf.INSTANCE_ID = 'i-test'
        os.environ['ALLOWED_CHAT_ID'] = '111'

    def tearDown(self):
        lf.tg_send = self._tg
        lf.INSTANCE_ID = self._id

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

    def test_plain_text_is_ignored(self):
        lf.lambda_handler(self._event('안녕'), None)
        self.assertEqual(self.sent, [])

    def test_command_failure_is_reported_not_raised(self):
        def boom(chat_id):
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


if __name__ == '__main__':
    unittest.main()

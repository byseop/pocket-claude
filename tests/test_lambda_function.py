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


if __name__ == '__main__':
    unittest.main()

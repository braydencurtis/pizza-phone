from __future__ import annotations

import os
import sys
import unittest
from io import StringIO

from agi.agi_channel import AGIChannel


class TestAGIChannel(unittest.TestCase):
    def setUp(self) -> None:
        # Save original stdin/stdout
        self._orig_stdin = sys.stdin
        self._orig_stdout = sys.stdout

    def tearDown(self) -> None:
        sys.stdin = self._orig_stdin
        sys.stdout = self._orig_stdout

    def _make_channel(
        self, env_overrides: dict[str, str] | None = None
    ) -> AGIChannel:
        """Create an AGIChannel with controlled env vars."""
        overrides = env_overrides or {}
        # Save original env
        saved = {}
        for key in overrides:
            saved[key] = os.environ.get(key, "")
        for k, v in overrides.items():
            os.environ[k] = v

        ch = AGIChannel()

        # Restore original env
        for key in overrides:
            if key in saved:
                os.environ[key] = saved[key]
            else:
                os.environ.pop(key, None)

        return ch

    def test_reads_agi_environment_variables(self) -> None:
        ch = self._make_channel(
            {
                "AGI_CHANNEL": "PJSIP/ht814-00000001",
                "AGI_UNIQUEID": "1721635200.1",
                "AGI_CALLERID": "+15551234567",
            }
        )
        self.assertEqual(ch.channel, "PJSIP/ht814-00000001")
        self.assertEqual(ch.uniqueid, "1721635200.1")
        self.assertEqual(ch.callerid, "+15551234567")

    def test_defaults_to_empty_when_env_missing(self) -> None:
        ch = self._make_channel({})
        self.assertEqual(ch.channel, "")
        self.assertEqual(ch.uniqueid, "")

    def test_command_sends_and_returns_response(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("200 result=ok\n")

        ch = AGIChannel()
        resp = ch.command("VERBOSE test message 1")

        self.assertEqual(resp, "200 result=ok")
        self.assertEqual(sys.stdout.getvalue(), "VERBOSE test message 1\n")

    def test_verbose_sends_verbose_command(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("200\n")

        ch = AGIChannel()
        ch.verbose("Hello, world", level=2)

        self.assertEqual(sys.stdout.getvalue(), "VERBOSE Hello, world 2\n")

    def test_stream_file_returns_digit_pressed(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("1\n")  # Caller pressed '1' during playback

        ch = AGIChannel()
        digit = ch.stream_file("welcome")

        self.assertEqual(digit, "1")
        self.assertEqual(sys.stdout.getvalue(), "STREAM FILE welcome \n")

    def test_stream_file_empty_string_when_no_digit(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("\n")

        ch = AGIChannel()
        digit = ch.stream_file("welcome")

        self.assertEqual(digit, "")

    def test_read_digits_returns_digits(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("200 result=\n200 result=1234\n")

        ch = AGIChannel()
        digits = ch.read_digits("silence/beam", 4, timeout=10000)

        self.assertEqual(digits, "1234")

    def test_read_digits_empty_on_no_input(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("200 result=\n200 result=\n")

        ch = AGIChannel()
        digits = ch.read_digits("silence/beam", 4, timeout=10000)

        self.assertEqual(digits, "")

    def test_exec_app_sends_exec_command(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("200\n")

        ch = AGIChannel()
        resp = ch.exec_app("Hangup", "")

        self.assertEqual(resp, "200")
        self.assertEqual(sys.stdout.getvalue(), "EXEC Hangup \n")

    def test_dial_uses_exec_app(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("200\n")

        ch = AGIChannel()
        ch.dial("PJSIP/200", timeout=30)

        sent = sys.stdout.getvalue()
        self.assertIn("Dial", sent)
        self.assertIn("PJSIP/200", sent)
        self.assertIn("30", sent)

    def test_set_variable(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("200\n")

        ch = AGIChannel()
        ch.set_variable("UPSTREAM_EXT", "200")

        self.assertEqual(sys.stdout.getvalue(), "SET VARIABLE UPSTREAM_EXT 200\n")

    def test_hangup(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("200\n")

        ch = AGIChannel()
        ch.hangup()

        self.assertIn("EXEC Hangup", sys.stdout.getvalue())

    def test_wait_ms(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("200\n")

        ch = AGIChannel()
        ch.wait_ms(500)

        self.assertIn("EXEC Wait 0.50", sys.stdout.getvalue())

    def test_say_number(self) -> None:
        sys.stdout = StringIO()
        sys.stdin = StringIO("3\n")

        ch = AGIChannel()
        digit = ch.say_number(42)

        self.assertEqual(digit, "3")
        self.assertIn("SAY NUMBER 42", sys.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

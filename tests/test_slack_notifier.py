from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


class TestSlackNotifier:

    def test_send_rotation_notice(self) -> None:
        from agi.slack_notifier import SlackNotifier

        notifier = SlackNotifier("https://hooks.slack.com/test")
        sent = notifier.send_rotation_notice("puzzle", "4242")
        assert sent is True

    def test_send_rotation_notice_payload(self) -> None:
        from agi.slack_notifier import SlackNotifier

        notifier = SlackNotifier("https://hooks.slack.com/test")
        with patch("agi.slack_notifier.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda self: self
            mock_urlopen.return_value.__exit__ = lambda self, *args: None
            notifier.send_rotation_notice("tweeted", "1234")
            request = mock_urlopen.call_args[0][0]
            payload = json.loads(request.data)
            assert payload["text"] == ":rotating_light: Pizza phone code rotated: 1234 (mode: tweeted)"

    def test_no_webhook_skips_notification(self) -> None:
        from agi.slack_notifier import SlackNotifier

        notifier = SlackNotifier(None)
        sent = notifier.send_rotation_notice("roguelike", "9999")
        assert sent is False

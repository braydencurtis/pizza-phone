from __future__ import annotations

import json
from typing import Optional
from urllib.request import Request, urlopen


class SlackNotifier:

    def __init__(self, webhook_url: Optional[str]) -> None:
        self.webhook_url = webhook_url

    def send_rotation_notice(self, mode: str, code: str) -> bool:
        if not self.webhook_url:
            return False

        payload = {
            "text": f":rotating_light: Pizza phone code rotated: {code} (mode: {mode})"
        }
        req = Request(
            self.webhook_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req)
        return True

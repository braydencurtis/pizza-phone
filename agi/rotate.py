from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from agi.code_manager import update_mode_and_code
from agi.slack_notifier import SlackNotifier
from agi.types import Mode


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate pizza phone code and mode")
    parser.add_argument("--mode", required=True, choices=["tweeted", "puzzle", "roguelike"], help="New mode (tweeted, puzzle, roguelike)")
    parser.add_argument("--code", required=True, help="New 4-digit code")
    parser.add_argument("--config", default=str(Path(__file__).parent.parent / "config" / "mode.json"), help="Path to mode.json")
    args = parser.parse_args()

    config_path = Path(args.config)
    update_mode_and_code(config_path, args.mode, args.code)

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    notifier = SlackNotifier(webhook_url)
    notifier.send_rotation_notice(args.mode, args.code)

    print(f"Rotated: mode={args.mode}, code={args.code}")


if __name__ == "__main__":
    main()

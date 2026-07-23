from __future__ import annotations

import os
import sys


class AGIChannel:
    """Handles communication with Asterisk over the AGI protocol.

    Asterisk launches the AGI script and populates environment variables
    with call metadata. Then it communicates via stdin/stdout using a
    simple command-response protocol.
    """

    env: dict[str, str]

    def __init__(self) -> None:
        self.env = {}
        self._read_env()

    def _read_env(self) -> None:
        """Read AGI environment variables set by Asterisk."""
        agi_vars = (
            "AGI_CHANNEL",
            "AGI_LANGUAGE",
            "AGI_TYPE",
            "AGI_UNIQUEID",
            "AGI_CALLERID",
            "AGI_CALLERIDNAME",
            "AGI_DNID",
            "AGI_RDNIS",
            "AGI_CONTEXT",
            "AGI_EXTEN",
            "AGI_PRIORITY",
            "AGI_ENHANCED",
            "AGI_ACCOUNT",
            "AGI_AGENT",
            "AGI_NETWORK",
        )
        for var in agi_vars:
            self.env[var] = os.environ.get(var, "")

    @property
    def channel(self) -> str:
        return self.env.get("AGI_CHANNEL", "")

    @property
    def uniqueid(self) -> str:
        return self.env.get("AGI_UNIQUEID", "")

    @property
    def callerid(self) -> str:
        return self.env.get("AGI_CALLERID", "")

    def command(self, cmd: str) -> str:
        """Send an AGI command and return the response line."""
        sys.stdout.write(cmd + "\n")
        sys.stdout.flush()
        resp = sys.stdin.readline().strip()
        return resp

    def verbose(self, message: str, level: int = 1) -> None:
        """Log a message to Asterisk console."""
        self.command(f"VERBOSE {message} {level}")

    def stream_file(self, filename: str, escape_keys: str = "") -> str:
        """Play an audio file. Returns the DTMF digit pressed during playback, or empty string."""
        resp = self.command(f"STREAM FILE {filename} {escape_keys}")
        return resp

    def say_number(self, number: int) -> str:
        """Speak a number. Returns DTMF digit pressed, or empty string."""
        resp = self.command(f"SAY NUMBER {number} ''")
        return resp

    def read_digits(
        self,
        filename: str,
        num_digits: int,
        timeout: int = 10000,
        escape_key: str = "",
    ) -> str:
        """Play a prompt and read DTMF digits.

        Returns the digits entered by the caller.
        """
        cmd = f"READ result {filename} {num_digits} {timeout}"
        if escape_key:
            cmd += f" {escape_key}"
        resp = self.command(cmd)
        # After READ, we need to retrieve the variable
        val_resp = self.command("GET VARIABLE result")
        # Response is either "200 result=..." or "200 result="
        if "result=" in val_resp:
            return val_resp.split("result=")[1].strip()
        return ""

    def exec_app(self, application: str, args: str = "") -> str:
        """Execute an Asterisk dialplan application."""
        resp = self.command(f"EXEC {application} {args}")
        return resp

    def set_variable(self, name: str, value: str) -> None:
        """Set a channel variable."""
        self.command(f"SET VARIABLE {name} {value}")

    def dial(self, destination: str, timeout: int = 30) -> str:
        """Dial a destination (e.g., PJSIP/200)."""
        return self.exec_app("Dial", f"{destination},{timeout}")

    def hangup(self) -> None:
        """Hang up the channel."""
        self.exec_app("Hangup", "")

    def wait_ms(self, ms: int) -> None:
        """Wait for a number of milliseconds."""
        self.exec_app("Wait", f"{ms / 1000:.2f}")

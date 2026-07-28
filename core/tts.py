from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Protocol


class TTSBackend(Protocol):
    def synthesize(self, text: str, output_path: Path) -> None: ...


class SayBackend:
    """macOS `say` + ffmpeg backend producing 8kHz phone-quality WAV."""

    def synthesize(self, text: str, output_path: Path) -> None:
        aiff_path = output_path.with_suffix(".aiff")
        subprocess.run(["say", "-o", str(aiff_path), text], check=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(aiff_path),
                "-acodec",
                "pcm_s16le",
                "-ar",
                "8000",
                "-ac",
                "1",
                str(output_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        aiff_path.unlink(missing_ok=True)


class EspeakBackend:
    """espeak backend producing 8kHz phone-quality WAV."""

    def synthesize(self, text: str, output_path: Path) -> None:
        subprocess.run(
            [
                "espeak",
                "-s", "130",
                "-a", "15",
                "-w", str(output_path),
                text,
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(output_path),
                "-acodec",
                "pcm_s16le",
                "-ar",
                "8000",
                "-ac",
                "1",
                str(output_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class FliteBackend:
    """flite backend producing 8kHz phone-quality WAV."""

    def synthesize(self, text: str, output_path: Path) -> None:
        subprocess.run(
            [
                "flite",
                "-tts",
                "-s", "130",
                "-o", str(output_path),
                text,
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(output_path),
                "-acodec",
                "pcm_s16le",
                "-ar",
                "8000",
                "-ac",
                "1",
                str(output_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


BACKEND_MAP: dict[str, type[TTSBackend]] = {
    "say": SayBackend,
    "espeak": EspeakBackend,
    "flite": FliteBackend,
}

BACKEND_PRIORITY: list[str] = ["espeak", "flite", "say"]


def detect_backend() -> type[TTSBackend]:
    """Auto-detect available TTS backend, highest priority first."""
    for name in BACKEND_PRIORITY:
        binary = "say" if name == "say" else name
        if shutil.which(binary):
            return BACKEND_MAP[name]
    raise RuntimeError(
        "No TTS backend available. Install one of: espeak, flite, or macOS say."
    )


def synthesize(
    text: str,
    backend: TTSBackend | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Generate WAV audio from text, returning the output file path."""
    if backend is None:
        backend = detect_backend()()

    if output_dir is None:
        output_dir = Path("/tmp")

    output_path = output_dir / f"tts_{uuid.uuid4().hex}.wav"
    backend.synthesize(text, output_path)
    return output_path

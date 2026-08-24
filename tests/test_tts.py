from __future__ import annotations

import shutil
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.tts import (
    EspeakBackend,
    FliteBackend,
    SayBackend,
    detect_backend,
    synthesize,
)


class WritingBackend:
    """A backend that just writes a file.

    ``synthesize`` is about naming and placing the output, not about any
    particular TTS binary, so its tests take a backend that needs nothing
    installed rather than shelling out to one that does.
    """

    def synthesize(self, text: str, output_path: Path) -> None:
        output_path.write_bytes(b"RIFF")


# The Espeak and Flite backends are tested against a mocked subprocess; these
# three actually run `say`, which exists only on macOS. The engine host is
# Linux (espeak/flite), so they skip everywhere but a Mac dev machine.
@pytest.mark.skipif(
    shutil.which("say") is None, reason="macOS `say` is not installed"
)
class TestSayBackend:

    def test_synthesize_produces_valid_wav(self, tmp_path: Path) -> None:
        backend = SayBackend()
        output = tmp_path / "test.wav"
        backend.synthesize("Hello world", output)

        assert output.exists()
        assert output.stat().st_size > 0

        with wave.open(str(output), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 8000

    def test_synthesize_different_texts_produce_different_sizes(
        self, tmp_path: Path
    ) -> None:
        backend = SayBackend()
        short = tmp_path / "short.wav"
        long = tmp_path / "long.wav"

        backend.synthesize("Hi", short)
        backend.synthesize("This is a longer sentence with more words to speak", long)

        assert long.stat().st_size > short.stat().st_size

    def test_synthesize_cleans_up_temp_aiff(self, tmp_path: Path) -> None:
        backend = SayBackend()
        output = tmp_path / "cleanup.wav"
        backend.synthesize("test", output)

        aiff_path = tmp_path / "cleanup.aiff"
        assert not aiff_path.exists()


class TestEspeakBackend:

    def test_synthesize_calls_espeak_with_correct_args(
        self, tmp_path: Path
    ) -> None:
        output = tmp_path / "test.wav"
        with patch("core.tts.subprocess.run") as mock_run:
            backend = EspeakBackend()
            backend.synthesize("Hello world", output)

            espeak_args = mock_run.call_args_list[0][0][0]
            assert "espeak" in espeak_args
            assert "-s" in espeak_args
            assert "130" in espeak_args
            assert "-w" in espeak_args
            assert str(output) in espeak_args
            assert "Hello world" in espeak_args

    def test_synthesize_resamples_to_8khz(self, tmp_path: Path) -> None:
        output = tmp_path / "test.wav"
        with patch("core.tts.subprocess.run") as mock_run:
            backend = EspeakBackend()
            backend.synthesize("test", output)

            ffmpeg_args = mock_run.call_args_list[1][0][0]
            assert "-ar" in ffmpeg_args
            assert "8000" in ffmpeg_args
            assert "-ac" in ffmpeg_args
            assert "1" in ffmpeg_args


class TestFliteBackend:

    def test_synthesize_calls_flite_with_correct_args(
        self, tmp_path: Path
    ) -> None:
        output = tmp_path / "test.wav"
        with patch("core.tts.subprocess.run") as mock_run:
            backend = FliteBackend()
            backend.synthesize("Hello world", output)

            flite_args = mock_run.call_args_list[0][0][0]
            assert "flite" in flite_args
            assert "-tts" in flite_args
            assert "-s" in flite_args
            assert "130" in flite_args
            assert "-o" in flite_args
            assert str(output) in flite_args
            assert "Hello world" in flite_args

    def test_synthesize_resamples_to_8khz(self, tmp_path: Path) -> None:
        output = tmp_path / "test.wav"
        with patch("core.tts.subprocess.run") as mock_run:
            backend = FliteBackend()
            backend.synthesize("test", output)

            ffmpeg_args = mock_run.call_args_list[1][0][0]
            assert "-ar" in ffmpeg_args
            assert "8000" in ffmpeg_args


class TestDetectBackend:

    def test_detect_espeak_when_available(self) -> None:
        with patch("core.tts.shutil.which") as mock_which:
            mock_which.side_effect = lambda x: "/usr/bin/" + x
            result = detect_backend()
            assert result == EspeakBackend

    def test_detect_flite_when_espeak_missing(self) -> None:
        def which_side_effect(binary: str):
            if binary == "espeak":
                return None
            return "/usr/bin/" + binary

        with patch("core.tts.shutil.which", side_effect=which_side_effect):
            result = detect_backend()
            assert result == FliteBackend

    def test_detect_say_when_others_missing(self) -> None:
        def which_side_effect(binary: str):
            if binary == "say":
                return "/usr/bin/say"
            return None

        with patch("core.tts.shutil.which", side_effect=which_side_effect):
            result = detect_backend()
            assert result == SayBackend

    def test_raises_when_no_backend_available(self) -> None:
        with (
            patch("core.tts.shutil.which", return_value=None),
            pytest.raises(RuntimeError, match="No TTS backend available"),
        ):
            detect_backend()


class TestSynthesize:

    def test_returns_wav_path(self, tmp_path: Path) -> None:
        backend = WritingBackend()
        result = synthesize("test", backend=backend, output_dir=tmp_path)

        assert result.suffix == ".wav"
        assert result.exists()

    def test_returns_path_in_output_dir(self, tmp_path: Path) -> None:
        backend = WritingBackend()
        result = synthesize("test", backend=backend, output_dir=tmp_path)

        assert str(tmp_path) in str(result)

    def test_auto_detects_backend_when_none_provided(self, tmp_path: Path) -> None:
        with patch("core.tts.detect_backend") as mock_detect:
            mock_detect.return_value = MagicMock
            synthesize("test", output_dir=tmp_path)
            mock_detect.assert_called_once()

    def test_allows_custom_backend_via_protocol(self, tmp_path: Path) -> None:
        mock_backend = MagicMock()
        with patch("core.tts.SayBackend"):
            synthesize("test", backend=mock_backend, output_dir=tmp_path)

            mock_backend.synthesize.assert_called_once()
            call_args = mock_backend.synthesize.call_args
            assert call_args[0][0] == "test"
            assert isinstance(call_args[0][1], Path)

    def test_raises_on_backend_failure(self, tmp_path: Path) -> None:
        failing_backend = MagicMock()
        failing_backend.synthesize.side_effect = RuntimeError("TTS failed")

        try:
            synthesize("test", backend=failing_backend, output_dir=tmp_path)
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "TTS failed" in str(e)

    def test_multiple_calls_produce_unique_files(self, tmp_path: Path) -> None:
        backend = WritingBackend()
        path1 = synthesize("test", backend=backend, output_dir=tmp_path)
        path2 = synthesize("test", backend=backend, output_dir=tmp_path)

        assert path1 != path2
        assert path1.exists()
        assert path2.exists()

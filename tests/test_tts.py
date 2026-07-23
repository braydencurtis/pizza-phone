from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from agi.tts import SayBackend, synthesize


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


class TestSynthesize:

    def test_returns_wav_path(self, tmp_path: Path) -> None:
        backend = SayBackend()
        result = synthesize("test", backend=backend, output_dir=tmp_path)

        assert result.suffix == ".wav"
        assert result.exists()

    def test_returns_path_in_output_dir(self, tmp_path: Path) -> None:
        backend = SayBackend()
        result = synthesize("test", backend=backend, output_dir=tmp_path)

        assert str(tmp_path) in str(result)

    def test_uses_default_backend_when_none_provided(self, tmp_path: Path) -> None:
        result = synthesize("test", output_dir=tmp_path)

        assert result.suffix == ".wav"
        assert result.exists()

    def test_allows_custom_backend_via_protocol(self, tmp_path: Path) -> None:
        mock_backend = MagicMock()
        # Patch synthesize to use our mock without calling subprocess
        with patch("agi.tts.SayBackend"):
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
        backend = SayBackend()
        path1 = synthesize("test", backend=backend, output_dir=tmp_path)
        path2 = synthesize("test", backend=backend, output_dir=tmp_path)

        assert path1 != path2
        assert path1.exists()
        assert path2.exists()

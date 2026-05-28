from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video_sum_core.errors import TranscriptionConfigurationError, VideoSumError
from video_sum_core.pipeline.real import PipelineSettings, RealPipelineRunner


class TestFunasrProvider:
    def test_funasr_routing(self, tmp_path):
        settings = PipelineSettings(
            tasks_dir=tmp_path,
            transcription_provider="funasr",
            local_asr_available=True,
            funasr_model="paraformer-zh",
            funasr_device="cpu",
        )
        runner = RealPipelineRunner(settings)

        audio_path = tmp_path / "test.wav"
        audio_path.write_text("dummy")

        with patch.object(runner, "_transcribe_with_funasr") as mock_funasr:
            mock_funasr.return_value = ("transcript", [{"start": 0, "end": 1, "text": "test"}])
            result = runner._transcribe(audio_path, None, lambda *args: None)
            mock_funasr.assert_called_once()
            assert result[0] == "transcript"

    def test_funasr_not_available_raises(self, tmp_path):
        settings = PipelineSettings(
            tasks_dir=tmp_path,
            transcription_provider="funasr",
            local_asr_available=False,
        )
        runner = RealPipelineRunner(settings)

        audio_path = tmp_path / "test.wav"
        audio_path.write_text("dummy")

        with pytest.raises(TranscriptionConfigurationError, match="not installed"):
            runner._transcribe_with_funasr(audio_path, None, lambda *args: None)

    def test_funasr_subprocess_success(self, tmp_path):
        settings = PipelineSettings(
            tasks_dir=tmp_path,
            transcription_provider="funasr",
            local_asr_available=True,
            funasr_available=True,
            funasr_model="paraformer-zh",
            funasr_device="cpu",
            funasr_vad_model="fsmn-vad",
            funasr_punc_model="ct-punc",
        )
        runner = RealPipelineRunner(settings)

        audio_path = tmp_path / "test.wav"
        audio_path.write_text("dummy")

        with patch.object(runner, "_run_funasr_subprocess") as mock_run:
            mock_run.return_value = ("测试转写", [{"start": 0, "end": 3, "text": "测试转写"}])
            transcript, segments = runner._transcribe_with_funasr(audio_path, None, lambda *args: None)
            assert transcript == "测试转写"
            assert len(segments) == 1
            mock_run.assert_called_once()

    def test_funasr_subprocess_retry_on_error(self, tmp_path):
        settings = PipelineSettings(
            tasks_dir=tmp_path,
            transcription_provider="funasr",
            local_asr_available=True,
            funasr_available=True,
            funasr_model="paraformer-zh",
            funasr_device="cpu",
            funasr_vad_model="fsmn-vad",
        )
        runner = RealPipelineRunner(settings)

        audio_path = tmp_path / "test.wav"
        audio_path.write_text("dummy")

        call_count = 0

        def mock_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise VideoSumError("native library level crash")
            return ("成功", [{"start": 0, "end": 2, "text": "成功"}])

        with patch.object(runner, "_run_funasr_subprocess") as mock_run:
            mock_run.side_effect = mock_run_side_effect
            transcript, segments = runner._transcribe_with_funasr(audio_path, None, lambda *args: None)
            assert transcript == "成功"
            assert call_count == 2

    def test_funasr_subprocess_no_retry_on_config_error(self, tmp_path):
        settings = PipelineSettings(
            tasks_dir=tmp_path,
            transcription_provider="funasr",
            local_asr_available=True,
            funasr_available=True,
            funasr_model="paraformer-zh",
            funasr_device="cpu",
        )
        runner = RealPipelineRunner(settings)

        audio_path = tmp_path / "test.wav"
        audio_path.write_text("dummy")

        with patch.object(runner, "_run_funasr_subprocess") as mock_run:
            mock_run.side_effect = TranscriptionConfigurationError("Invalid config")
            with pytest.raises(TranscriptionConfigurationError):
                runner._transcribe_with_funasr(audio_path, None, lambda *args: None)
            assert mock_run.call_count == 1

    def test_build_funasr_command(self, tmp_path):
        settings = PipelineSettings(
            tasks_dir=tmp_path,
            runtime_channel="base",
            funasr_model="paraformer-zh",
            funasr_device="cpu",
            funasr_vad_model="fsmn-vad",
            funasr_punc_model="ct-punc",
            funasr_spk_model="cam++",
            funasr_hub="ms",
            funasr_hotword="热词1 热词2",
        )
        runner = RealPipelineRunner(settings)

        audio_path = tmp_path / "test.wav"
        progress_path = tmp_path / "progress.jsonl"
        output_path = tmp_path / "output.json"

        with patch("video_sum_core.pipeline.real.runtime_python_executable") as mock_python:
            mock_python.return_value = Path("/usr/bin/python")
            command = runner._build_funasr_command(
                audio_path=audio_path,
                model_name="paraformer-zh",
                device="cpu",
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                spk_model="cam++",
                hub="ms",
                hotword="热词1 热词2",
                progress_path=progress_path,
                output_path=output_path,
            )

        assert command[0] == "/usr/bin/python"
        assert "-m" in command
        assert "video_sum_core.transcribe_funasr_subprocess" in command
        assert "--audio-path" in command
        assert "--model" in command
        assert "paraformer-zh" in command
        assert "--device" in command
        assert "cpu" in command
        assert "--vad-model" in command
        assert "fsmn-vad" in command
        assert "--punc-model" in command
        assert "ct-punc" in command
        assert "--spk-model" in command
        assert "cam++" in command
        assert "--hub" in command
        assert "ms" in command
        assert "--hotword" in command
        assert "热词1 热词2" in command

    def test_run_funasr_subprocess_timeout(self, tmp_path):
        settings = PipelineSettings(
            tasks_dir=tmp_path,
            runtime_channel="base",
            funasr_model="paraformer-zh",
            funasr_device="cpu",
        )
        runner = RealPipelineRunner(settings)

        audio_path = tmp_path / "test.wav"
        audio_path.write_text("dummy")

        with patch.object(runner, "_build_funasr_command") as mock_build:
            mock_build.return_value = ["sleep", "9999"]
            with patch("video_sum_core.pipeline.real.subprocess.Popen") as mock_popen:
                mock_process = MagicMock()
                mock_process.poll.return_value = None
                mock_process.communicate.return_value = ("", "")
                mock_popen.return_value = mock_process

                with patch("video_sum_core.pipeline.real.time.monotonic") as mock_time:
                    mock_time.side_effect = [0, 0, 9999]

                    with pytest.raises(VideoSumError, match="timed out"):
                        runner._run_funasr_subprocess(
                            audio_path=audio_path,
                            duration=1.0,
                            emit=lambda *args: None,
                            model_name="paraformer-zh",
                            device="cpu",
                            vad_model="",
                            punc_model="",
                            spk_model="",
                            hub="ms",
                            hotword="",
                        )
                    mock_process.kill.assert_called_once()

    def test_run_funasr_subprocess_crash_with_valid_output(self, tmp_path):
        settings = PipelineSettings(
            tasks_dir=tmp_path,
            runtime_channel="base",
            funasr_model="paraformer-zh",
            funasr_device="cpu",
        )
        runner = RealPipelineRunner(settings)

        audio_path = tmp_path / "test.wav"
        audio_path.write_text("dummy")
        output_path = audio_path.with_name("funasr_worker_result.json")
        output_path.write_text(
            json.dumps(
                {
                    "transcript": "[00:00] 测试",
                    "segments": [{"start": 0, "end": 2, "text": "测试"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch.object(output_path, "unlink"):  # keep file alive through cleanup
            with patch.object(output_path, "exists", return_value=True):
                with patch.object(runner, "_build_funasr_command") as mock_build:
                    mock_build.return_value = ["echo", "test"]
                    with patch("video_sum_core.pipeline.real.subprocess.Popen") as mock_popen:
                        mock_process = MagicMock()
                        mock_process.poll.return_value = 3221226505
                        mock_process.returncode = 3221226505
                        mock_process.communicate.return_value = ("", "")
                        mock_popen.return_value = mock_process

                        transcript, segments = runner._run_funasr_subprocess(
                            audio_path=audio_path,
                            duration=None,
                            emit=lambda *args: None,
                            model_name="paraformer-zh",
                            device="cpu",
                            vad_model="",
                            punc_model="",
                            spk_model="",
                            hub="ms",
                            hotword="",
                        )

                        assert transcript == "[00:00] 测试"
                        assert len(segments) == 1

    def test_run_funasr_subprocess_crash_without_output(self, tmp_path):
        settings = PipelineSettings(
            tasks_dir=tmp_path,
            runtime_channel="base",
            funasr_model="paraformer-zh",
            funasr_device="cpu",
        )
        runner = RealPipelineRunner(settings)

        audio_path = tmp_path / "test.wav"
        audio_path.write_text("dummy")

        with patch.object(runner, "_build_funasr_command") as mock_build:
            mock_build.return_value = ["false"]
            with patch("video_sum_core.pipeline.real.subprocess.Popen") as mock_popen:
                mock_process = MagicMock()
                mock_process.poll.return_value = 1
                mock_process.returncode = 1
                mock_process.communicate.return_value = ("", "error")
                mock_popen.return_value = mock_process

                with pytest.raises(VideoSumError, match="failed with exit code"):
                    runner._run_funasr_subprocess(
                        audio_path=audio_path,
                        duration=None,
                        emit=lambda *args: None,
                        model_name="paraformer-zh",
                        device="cpu",
                        vad_model="",
                        punc_model="",
                        spk_model="",
                        hub="ms",
                        hotword="",
                    )

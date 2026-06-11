"""
Testes do módulo recorder — verifica lógica pura (comandos ffmpeg, detecção
de codec, formato nativo, caminhos de falha) sem display ou hardware real.
"""
import sys
import os
import types
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# ── Stubs Qt mínimos ──────────────────────────────────────────────────────────

def _install_qt_stubs():
    def _mod(name):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
        return sys.modules[name]

    _mod("PyQt6")

    qtw = _mod("PyQt6.QtWidgets")
    qtw.QApplication = MagicMock()
    qtw.QApplication.screens = MagicMock(return_value=[])
    qtw.QApplication.primaryScreen = MagicMock(return_value=None)

    qtc = _mod("PyQt6.QtCore")

    class _FakeQObject:
        def __init__(self, parent=None):
            pass

    class _BoundSignal:
        def __init__(self):
            self._cbs = []
        def connect(self, cb):
            self._cbs.append(cb)
        def emit(self, *a):
            for cb in self._cbs:
                cb(*a)

    class _SignalDescriptor:
        def __set_name__(self, owner, name):
            self._name = name
        def __get__(self, obj, cls):
            if obj is None:
                return self
            k = f"_sig_{self._name}"
            if not hasattr(obj, k):
                setattr(obj, k, _BoundSignal())
            return getattr(obj, k)

    def _pyqtSignal(*args, **kwargs):
        return _SignalDescriptor()

    qtc.QObject = _FakeQObject
    qtc.pyqtSignal = _pyqtSignal

    # Multimedia stubs
    qtm = _mod("PyQt6.QtMultimedia")
    qtm.QScreenCapture = MagicMock
    qtm.QMediaCaptureSession = MagicMock
    qtm.QVideoSink = MagicMock
    qtm.QVideoFrame = MagicMock

    class _PixFmt:
        Format_BGRA8888 = "Format_BGRA8888"
        Format_BGRA8888_Premultiplied = "Format_BGRA8888_Premultiplied"
        Format_RGBA8888 = "Format_RGBA8888"
        Format_RGBX8888 = "Format_RGBX8888"

    class _VideoFrameFormat:
        PixelFormat = _PixFmt()

    qtm.QVideoFrameFormat = _VideoFrameFormat

    qtg = _mod("PyQt6.QtGui")
    qtg.QImage = MagicMock

_install_qt_stubs()

import recorder as rec  # noqa: E402


# ── _find_ffmpeg ──────────────────────────────────────────────────────────────

def test_find_ffmpeg_returns_bundled_when_appdir_set(tmp_path, monkeypatch):
    fake_ffmpeg = tmp_path / "usr" / "bin" / "ffmpeg"
    fake_ffmpeg.parent.mkdir(parents=True)
    fake_ffmpeg.touch()
    fake_ffmpeg.chmod(0o755)
    monkeypatch.setenv("APPDIR", str(tmp_path))
    monkeypatch.setattr(rec, "_which", lambda t: "/usr/bin/ffmpeg")
    assert rec._find_ffmpeg() == str(fake_ffmpeg)


def test_find_ffmpeg_falls_back_to_system(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDIR", str(tmp_path))
    monkeypatch.setattr(rec, "_which", lambda t: "/usr/bin/ffmpeg" if t == "ffmpeg" else None)
    assert rec._find_ffmpeg() == "/usr/bin/ffmpeg"


def test_find_ffmpeg_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDIR", str(tmp_path))
    monkeypatch.setattr(rec, "_which", lambda t: None)
    assert rec._find_ffmpeg() is None


def test_find_ffmpeg_no_appdir(monkeypatch):
    monkeypatch.delenv("APPDIR", raising=False)
    monkeypatch.setattr(rec, "_which", lambda t: "/usr/local/bin/ffmpeg" if t == "ffmpeg" else None)
    assert rec._find_ffmpeg() == "/usr/local/bin/ffmpeg"


# ── _has_libx264 ──────────────────────────────────────────────────────────────

def test_has_libx264_true(monkeypatch):
    r = MagicMock()
    r.stdout = " V..... libx264   libx264 H.264 / AVC / MPEG-4 AVC"
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=r))
    assert rec._has_libx264("/usr/bin/ffmpeg") is True


def test_has_libx264_false(monkeypatch):
    r = MagicMock()
    r.stdout = " V..... mpeg4   MPEG-4 part 2"
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=r))
    assert rec._has_libx264("/usr/bin/ffmpeg") is False


def test_has_libx264_exception_returns_false(monkeypatch):
    monkeypatch.setattr("subprocess.run", MagicMock(side_effect=OSError("nope")))
    assert rec._has_libx264("/usr/bin/ffmpeg") is False


# ── _native_pix_fmt ───────────────────────────────────────────────────────────

def test_native_pix_fmt_bgra(monkeypatch):
    monkeypatch.setattr(rec, "_NATIVE_FMTS", None)
    frame = MagicMock()
    frame.pixelFormat.return_value = "Format_BGRA8888"
    assert rec._native_pix_fmt(frame) == "bgra"


def test_native_pix_fmt_rgba(monkeypatch):
    monkeypatch.setattr(rec, "_NATIVE_FMTS", None)
    frame = MagicMock()
    frame.pixelFormat.return_value = "Format_RGBA8888"
    assert rec._native_pix_fmt(frame) == "rgba"


def test_native_pix_fmt_unknown_defaults_rgba(monkeypatch):
    monkeypatch.setattr(rec, "_NATIVE_FMTS", None)
    frame = MagicMock()
    frame.pixelFormat.return_value = "Format_NV12"
    assert rec._native_pix_fmt(frame) == "rgba"


# ── _build_ffmpeg_cmd (com x264) ──────────────────────────────────────────────

def test_build_x264_contains_ultrafast():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    assert "ultrafast" in cmd
    assert "libx264" in cmd


def test_build_x264_contains_fastdecode():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    assert "fastdecode" in cmd


def test_build_x264_contains_resolution():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1280, 720, 60, "/tmp/out.mp4", True)
    assert "1280x720" in cmd
    assert "60" in cmd


def test_build_x264_contains_dest():
    dest = "/home/user/Vídeos/EpicPen/rec.mp4"
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, dest, True)
    assert dest in cmd


def test_build_default_pix_fmt_is_rgba():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    idx = cmd.index("-pixel_format")
    assert cmd[idx + 1] == "rgba"


def test_build_custom_pix_fmt_bgra():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True, "bgra")
    idx = cmd.index("-pixel_format")
    assert cmd[idx + 1] == "bgra"


def test_build_x264opts_present():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    assert "-x264opts" in cmd
    opts = cmd[cmd.index("-x264opts") + 1]
    assert "sliced-threads" in opts
    assert "aq-mode=0" in opts
    assert "threads=0" in opts


def test_build_x264_faststart():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    assert "+faststart" in cmd


def test_build_x264_no_audio():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    assert "-an" in cmd


def test_build_x264_profile_baseline():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    assert "baseline" in cmd


def test_build_wallclock_timestamps():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    assert "-use_wallclock_as_timestamps" in cmd


# ── _build_ffmpeg_cmd (sem x264, fallback mpeg4) ─────────────────────────────

def test_build_no_x264_uses_mpeg4():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", False)
    assert "mpeg4" in cmd
    assert "libx264" not in cmd


def test_build_no_x264_no_x264opts():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", False)
    assert "-x264opts" not in cmd


# ── ScreenRecorder.start: caminhos de falha ───────────────────────────────────

def test_start_returns_false_when_no_ffmpeg(monkeypatch):
    monkeypatch.setattr(rec, "_find_ffmpeg", lambda: None)
    recorder = rec.ScreenRecorder()
    recorder.failed = MagicMock()
    assert recorder.start() is False
    recorder.failed.emit.assert_called_once()
    assert "ffmpeg" in recorder.failed.emit.call_args[0][0].lower()


def test_start_returns_false_when_no_screen(monkeypatch):
    monkeypatch.setattr(rec, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(rec, "_best_screen", lambda: None)
    recorder = rec.ScreenRecorder()
    recorder.failed = MagicMock()
    assert recorder.start() is False
    recorder.failed.emit.assert_called_once()


def test_start_returns_true_when_already_recording(monkeypatch):
    recorder = rec.ScreenRecorder()
    recorder._active = True
    assert recorder.start() is True


# ── ScreenRecorder._start_ffmpeg: falha no Popen ─────────────────────────────

def test_start_ffmpeg_emits_failed_on_popen_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "subprocess.Popen", MagicMock(side_effect=OSError("permission denied"))
    )
    recorder = rec.ScreenRecorder()
    recorder._ffmpeg_path = "/usr/bin/ffmpeg"
    recorder._rec_w = 1920
    recorder._rec_h = 1080
    recorder._rec_fps = 30
    recorder._rec_has_x264 = True
    recorder._dest = tmp_path / "out.mp4"
    recorder.failed = MagicMock()
    assert recorder._start_ffmpeg("rgba") is False
    recorder.failed.emit.assert_called_once()


# ── ScreenRecorder.stop: casos de borda ──────────────────────────────────────

def test_stop_when_not_recording_is_noop():
    recorder = rec.ScreenRecorder()
    recorder.stopped = MagicMock()
    recorder.failed = MagicMock()
    recorder.stop()
    recorder.stopped.emit.assert_not_called()
    recorder.failed.emit.assert_not_called()


def test_stop_when_ffmpeg_never_started_emits_failed():
    recorder = rec.ScreenRecorder()
    recorder._active = True
    recorder._proc = None
    recorder.stopped = MagicMock()
    recorder.failed = MagicMock()
    recorder.stop()
    recorder.failed.emit.assert_called_once()
    recorder.stopped.emit.assert_not_called()

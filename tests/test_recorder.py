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

    class _VideoFrame:
        class MapMode:
            ReadOnly = 1

    qtm.QVideoFrame = _VideoFrame

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

def _make_exe(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(0o755)
    return str(path)


def test_find_ffmpeg_returns_bundled_when_appdir_set(tmp_path, monkeypatch):
    bundled = _make_exe(tmp_path / "usr" / "bin" / "ffmpeg")
    system = _make_exe(tmp_path / "sys" / "ffmpeg")
    monkeypatch.setenv("APPDIR", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda t: system)
    monkeypatch.setattr(rec, "_EXTRA_PATHS", [])
    assert rec._find_ffmpeg() == bundled


def test_find_ffmpeg_falls_back_to_system(tmp_path, monkeypatch):
    system = _make_exe(tmp_path / "sys" / "ffmpeg")
    monkeypatch.setenv("APPDIR", str(tmp_path))  # sem bundled dentro
    monkeypatch.setattr("shutil.which", lambda t: system)
    monkeypatch.setattr(rec, "_EXTRA_PATHS", [])
    assert rec._find_ffmpeg() == system


def test_find_ffmpeg_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDIR", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda t: None)
    monkeypatch.setattr(rec, "_EXTRA_PATHS", [])
    assert rec._find_ffmpeg() is None


def test_find_ffmpeg_no_appdir(tmp_path, monkeypatch):
    system = _make_exe(tmp_path / "local" / "ffmpeg")
    monkeypatch.delenv("APPDIR", raising=False)
    monkeypatch.setattr("shutil.which", lambda t: system)
    monkeypatch.setattr(rec, "_EXTRA_PATHS", [])
    assert rec._find_ffmpeg() == system


def test_candidates_include_extra_paths(tmp_path, monkeypatch):
    """ffmpeg da distro entra mesmo sombreado por outro no PATH (ex.: brew)."""
    brew = _make_exe(tmp_path / "brew" / "ffmpeg")
    distro = _make_exe(tmp_path / "distro" / "ffmpeg")
    monkeypatch.delenv("APPDIR", raising=False)
    monkeypatch.setattr("shutil.which", lambda t: brew)
    monkeypatch.setattr(rec, "_EXTRA_PATHS", [str(tmp_path / "distro")])
    assert rec._ffmpeg_candidates() == [brew, distro]


def test_candidates_dedupe_symlinks(tmp_path, monkeypatch):
    """/bin/ffmpeg symlink de /usr/bin/ffmpeg não duplica candidato."""
    real = _make_exe(tmp_path / "usr" / "ffmpeg")
    link_dir = tmp_path / "bin"
    link_dir.mkdir()
    (link_dir / "ffmpeg").symlink_to(real)
    monkeypatch.delenv("APPDIR", raising=False)
    monkeypatch.setattr("shutil.which", lambda t: real)
    monkeypatch.setattr(rec, "_EXTRA_PATHS", [str(link_dir)])
    assert rec._ffmpeg_candidates() == [real]


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


def test_build_uses_millisecond_timebase():
    """-framerate declarado = 1000 (timebase 1ms): timestamps wallclock em
    timebase grosso colidem em rajadas e o vsync vfr descarta frames."""
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 144, "/tmp/out.mkv", True)
    idx = cmd.index("-framerate")
    assert cmd[idx + 1] == "1000"


def test_build_video_cmd_has_copyts():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 60, "/tmp/out.mkv", True)
    assert "-copyts" in cmd


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


def test_build_video_cmd_never_muxes_audio():
    """Vídeo e áudio NUNCA no mesmo processo: mux ao vivo trava o pipe a ~23fps."""
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mkv", True)
    assert "-an" in cmd
    assert "pulse" not in cmd
    assert "+faststart" not in cmd  # faststart só na montagem final (MP4)


def test_build_x264_profile_baseline():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    assert "baseline" in cmd


def test_build_wallclock_timestamps():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    assert "-use_wallclock_as_timestamps" in cmd


def test_build_vsync_vfr_present():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True)
    idx = cmd.index("-vsync")
    assert cmd[idx + 1] == "vfr"


# ── _build_ffmpeg_cmd (VAAPI) ─────────────────────────────────────────────────

def test_build_vaapi_uses_h264_vaapi():
    cmd = rec._build_ffmpeg_cmd(
        "/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True,
        vaapi_device="/dev/dri/renderD128",
    )
    assert "h264_vaapi" in cmd
    assert "libx264" not in cmd


def test_build_vaapi_init_hw_device():
    cmd = rec._build_ffmpeg_cmd(
        "/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True,
        vaapi_device="/dev/dri/renderD128",
    )
    idx = cmd.index("-init_hw_device")
    assert cmd[idx + 1] == "vaapi=va:/dev/dri/renderD128"
    assert "-filter_hw_device" in cmd


def test_build_vaapi_hwupload_filter():
    cmd = rec._build_ffmpeg_cmd(
        "/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True,
        vaapi_device="/dev/dri/renderD128",
    )
    idx = cmd.index("-vf")
    assert "hwupload" in cmd[idx + 1]


def test_build_vaapi_takes_priority_over_x264():
    cmd = rec._build_ffmpeg_cmd(
        "/usr/bin/ffmpeg", 1920, 1080, 30, "/tmp/out.mp4", True,
        vaapi_device="/dev/dri/renderD129",
    )
    assert "-x264opts" not in cmd


# ── _probe_vaapi ──────────────────────────────────────────────────────────────

def test_probe_vaapi_returns_device_on_success(monkeypatch):
    monkeypatch.setattr(rec, "_VAAPI_CACHE", {})
    monkeypatch.setattr(
        "glob.glob", lambda p: ["/dev/dri/renderD128", "/dev/dri/renderD129"]
    )
    r = MagicMock()
    r.returncode = 0
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=r))
    assert rec._probe_vaapi("/usr/bin/ffmpeg") == "/dev/dri/renderD128"


def test_probe_vaapi_returns_none_when_encode_fails(monkeypatch):
    monkeypatch.setattr(rec, "_VAAPI_CACHE", {})
    monkeypatch.setattr("glob.glob", lambda p: ["/dev/dri/renderD128"])
    r = MagicMock()
    r.returncode = 1
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=r))
    assert rec._probe_vaapi("/usr/bin/ffmpeg") is None


def test_probe_vaapi_returns_none_without_devices(monkeypatch):
    monkeypatch.setattr(rec, "_VAAPI_CACHE", {})
    monkeypatch.setattr("glob.glob", lambda p: [])
    assert rec._probe_vaapi("/usr/bin/ffmpeg") is None


def test_probe_vaapi_caches_result(monkeypatch):
    monkeypatch.setattr(rec, "_VAAPI_CACHE", {})
    monkeypatch.setattr("glob.glob", lambda p: ["/dev/dri/renderD128"])
    r = MagicMock()
    r.returncode = 0
    run = MagicMock(return_value=r)
    monkeypatch.setattr("subprocess.run", run)
    rec._probe_vaapi("/usr/bin/ffmpeg")
    rec._probe_vaapi("/usr/bin/ffmpeg")
    assert run.call_count == 1


def test_probe_vaapi_survives_exception(monkeypatch):
    monkeypatch.setattr(rec, "_VAAPI_CACHE", {})
    monkeypatch.setattr("glob.glob", lambda p: ["/dev/dri/renderD128"])
    monkeypatch.setattr("subprocess.run", MagicMock(side_effect=OSError("boom")))
    assert rec._probe_vaapi("/usr/bin/ffmpeg") is None


# ── _pick_ffmpeg ──────────────────────────────────────────────────────────────

def test_pick_prefers_vaapi(monkeypatch):
    monkeypatch.setattr(rec, "_ffmpeg_candidates", lambda: ["/app/ffmpeg", "/usr/bin/ffmpeg"])
    monkeypatch.setattr(
        rec, "_probe_vaapi",
        lambda f: "/dev/dri/renderD128" if f == "/usr/bin/ffmpeg" else None,
    )
    monkeypatch.setattr(rec, "_has_libx264", lambda f: True)
    monkeypatch.setattr(rec, "_has_audio_support", lambda f: False)
    path, dev, x264, audio = rec._pick_ffmpeg()
    assert path == "/usr/bin/ffmpeg"
    assert dev == "/dev/dri/renderD128"
    assert x264 is True
    assert audio is False


def test_pick_falls_back_to_x264(monkeypatch):
    monkeypatch.setattr(rec, "_ffmpeg_candidates", lambda: ["/app/ffmpeg", "/usr/bin/ffmpeg"])
    monkeypatch.setattr(rec, "_probe_vaapi", lambda f: None)
    monkeypatch.setattr(rec, "_has_libx264", lambda f: f == "/app/ffmpeg")
    monkeypatch.setattr(rec, "_has_audio_support", lambda f: False)
    path, dev, x264, audio = rec._pick_ffmpeg()
    assert path == "/app/ffmpeg"
    assert dev is None
    assert x264 is True


def test_pick_last_resort_mpeg4(monkeypatch):
    monkeypatch.setattr(rec, "_ffmpeg_candidates", lambda: ["/usr/bin/ffmpeg"])
    monkeypatch.setattr(rec, "_probe_vaapi", lambda f: None)
    monkeypatch.setattr(rec, "_has_libx264", lambda f: False)
    monkeypatch.setattr(rec, "_has_audio_support", lambda f: False)
    path, dev, x264, audio = rec._pick_ffmpeg()
    assert path == "/usr/bin/ffmpeg"
    assert dev is None
    assert x264 is False


def test_pick_audio_outweighs_vaapi(monkeypatch):
    """Sistema com pulse+aac ganha do bundled com VAAPI: áudio pesa mais."""
    monkeypatch.setattr(rec, "_ffmpeg_candidates", lambda: ["/app/ffmpeg", "/usr/bin/ffmpeg"])
    monkeypatch.setattr(
        rec, "_probe_vaapi",
        lambda f: "/dev/dri/renderD128" if f == "/app/ffmpeg" else None,
    )
    monkeypatch.setattr(rec, "_has_libx264", lambda f: True)
    monkeypatch.setattr(rec, "_has_audio_support", lambda f: f == "/usr/bin/ffmpeg")
    path, dev, x264, audio = rec._pick_ffmpeg()
    assert path == "/usr/bin/ffmpeg"
    assert audio is True


def test_pick_returns_none_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(rec, "_ffmpeg_candidates", lambda: [])
    assert rec._pick_ffmpeg() == (None, None, False, False)


# ── Áudio: _build_audio_cmd (processo separado) ──────────────────────────────

def test_audio_cmd_two_devices_uses_amix():
    cmd = rec._build_audio_cmd(
        "/usr/bin/ffmpeg", ["mic_dev", "sink_dev.monitor"], "/tmp/aud.mka",
    )
    assert cmd.count("pulse") == 2
    assert "mic_dev" in cmd
    assert "sink_dev.monitor" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "amix=inputs=2" in fc
    assert "aac" in cmd
    assert "-copyts" in cmd


def test_audio_cmd_one_device_maps_directly():
    cmd = rec._build_audio_cmd("/usr/bin/ffmpeg", ["mic_dev"], "/tmp/aud.mka")
    assert "-filter_complex" not in cmd
    assert cmd[cmd.index("-map") + 1] == "0:a"
    assert "aac" in cmd


def test_audio_cmd_uses_wallclock():
    cmd = rec._build_audio_cmd("/usr/bin/ffmpeg", ["mic_dev"], "/tmp/aud.mka")
    assert "-use_wallclock_as_timestamps" in cmd


def test_audio_cmd_duck_uses_sidechain():
    """Ducking: o som do sistema é comprimido pelo sinal do mic."""
    cmd = rec._build_audio_cmd(
        "/usr/bin/ffmpeg", ["mic_dev", "sink.monitor"], "/tmp/aud.mka",
        duck=True,
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "sidechaincompress" in fc
    assert "amix=inputs=2" in fc
    assert fc.startswith("[0:a]asplit")  # mic alimenta o sidechain


def test_audio_cmd_duck_ignored_with_one_device():
    cmd = rec._build_audio_cmd(
        "/usr/bin/ffmpeg", ["mic_dev"], "/tmp/aud.mka", duck=True,
    )
    assert "-filter_complex" not in cmd


def test_has_filter_true(monkeypatch):
    monkeypatch.setattr(rec, "_FILTER_CACHE", {})
    r = MagicMock()
    r.stdout = " ..C sidechaincompress AA->A  Sidechain compressor."
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=r))
    assert rec._has_filter("/usr/bin/ffmpeg", "sidechaincompress") is True


def test_has_filter_false_and_cached(monkeypatch):
    monkeypatch.setattr(rec, "_FILTER_CACHE", {})
    r = MagicMock()
    r.stdout = " ..C amix  N->A  Mix."
    run = MagicMock(return_value=r)
    monkeypatch.setattr("subprocess.run", run)
    assert rec._has_filter("/usr/bin/ffmpeg", "sidechaincompress") is False
    rec._has_filter("/usr/bin/ffmpeg", "sidechaincompress")
    assert run.call_count == 1


# ── _build_remux_cmd (montagem final) ─────────────────────────────────────────

def test_remux_with_audio_copies_both():
    cmd = rec._build_remux_cmd("/usr/bin/ffmpeg", "/tmp/v.mkv", "/tmp/a.mka", "/tmp/f.mp4")
    assert "/tmp/v.mkv" in cmd
    assert "/tmp/a.mka" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "copy"
    assert "-copyts" in cmd
    assert "make_zero" in cmd
    assert "+faststart" in cmd


def test_remux_audio_skip_trims_lead():
    cmd = rec._build_remux_cmd(
        "/usr/bin/ffmpeg", "/tmp/v.mkv", "/tmp/a.mka", "/tmp/f.mp4",
        audio_skip=2.633,
    )
    idx = cmd.index("-ss")
    assert cmd[idx + 1] == "2.633"
    assert cmd[idx + 2] == "-i"
    assert cmd[idx + 3] == "/tmp/a.mka"


def test_remux_no_skip_when_negligible():
    cmd = rec._build_remux_cmd(
        "/usr/bin/ffmpeg", "/tmp/v.mkv", "/tmp/a.mka", "/tmp/f.mp4",
        audio_skip=0.005,
    )
    assert "-ss" not in cmd


def test_remux_without_audio():
    cmd = rec._build_remux_cmd("/usr/bin/ffmpeg", "/tmp/v.mkv", None, "/tmp/f.mp4")
    assert "/tmp/v.mkv" in cmd
    assert "-c:a" not in cmd
    assert "1:a" not in cmd


# ── _has_audio_support ────────────────────────────────────────────────────────

def test_has_audio_support_true(monkeypatch):
    def _run(cmd, **kw):
        r = MagicMock()
        r.stdout = " D  pulse  Pulse audio" if "-devices" in cmd else " A..... aac  AAC"
        return r
    monkeypatch.setattr("subprocess.run", _run)
    assert rec._has_audio_support("/usr/bin/ffmpeg") is True


def test_has_audio_support_false_without_pulse(monkeypatch):
    def _run(cmd, **kw):
        r = MagicMock()
        r.stdout = " D  alsa  ALSA" if "-devices" in cmd else " A..... aac  AAC"
        return r
    monkeypatch.setattr("subprocess.run", _run)
    assert rec._has_audio_support("/usr/bin/ffmpeg") is False


def test_has_audio_support_false_without_aac(monkeypatch):
    def _run(cmd, **kw):
        r = MagicMock()
        r.stdout = " D  pulse  Pulse audio" if "-devices" in cmd else " A..... mp2  MP2"
        return r
    monkeypatch.setattr("subprocess.run", _run)
    assert rec._has_audio_support("/usr/bin/ffmpeg") is False


def test_has_audio_support_exception_returns_false(monkeypatch):
    monkeypatch.setattr("subprocess.run", MagicMock(side_effect=OSError("boom")))
    assert rec._has_audio_support("/usr/bin/ffmpeg") is False


# ── _default_audio_devices ────────────────────────────────────────────────────

def test_default_audio_devices_mic_and_monitor(monkeypatch):
    def _run(cmd, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stdout = "mic_src\n" if "get-default-source" in cmd else "spk_sink\n"
        return r
    monkeypatch.setattr("subprocess.run", _run)
    assert rec._default_audio_devices() == ["mic_src", "spk_sink.monitor"]


def test_default_audio_devices_dedupes_monitor_as_mic(monkeypatch):
    def _run(cmd, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stdout = "spk_sink.monitor\n" if "get-default-source" in cmd else "spk_sink\n"
        return r
    monkeypatch.setattr("subprocess.run", _run)
    assert rec._default_audio_devices() == ["spk_sink.monitor"]


def test_default_audio_devices_empty_without_pactl(monkeypatch):
    monkeypatch.setattr("subprocess.run", MagicMock(side_effect=FileNotFoundError))
    assert rec._default_audio_devices() == []


# ── Estratégia adaptativa (gpu / cpu / disk) ──────────────────────────────────

_GB = 1 << 30


def test_strategy_gpu_when_vaapi():
    assert rec._pick_strategy("/dev/dri/renderD128", True, 2,
                              500_000_000, 100 * _GB, True) == "gpu"


def test_strategy_cpu_when_many_cores():
    assert rec._pick_strategy(None, True, 8,
                              500_000_000, 100 * _GB, True) == "cpu"


def test_strategy_disk_when_weak_cpu_and_fast_disk():
    assert rec._pick_strategy(None, True, 4,
                              500_000_000, 500 * _GB, True) == "disk"


def test_strategy_cpu_when_weak_cpu_but_slow_disk():
    assert rec._pick_strategy(None, True, 4,
                              500_000_000, 500 * _GB, False) == "cpu"


def test_strategy_cpu_when_weak_cpu_but_no_space():
    assert rec._pick_strategy(None, True, 2,
                              500_000_000, 1 * _GB, True) == "cpu"


# ── _queue_frames (fila dimensionada pela RAM) ────────────────────────────────

def test_queue_frames_minimum_is_4():
    # 8GB de frame (absurdo) com pouca RAM → mínimo 4
    assert rec._queue_frames(8 * _GB, 1 * _GB) == 4


def test_queue_frames_scales_with_ram():
    # frame 1080p (~8MB) com 8GB livres → 25% = 2GB → 240 (teto)
    assert rec._queue_frames(8_294_400, 8 * _GB) == 240


def test_queue_frames_capped_at_240():
    assert rec._queue_frames(1024, 64 * _GB) == 240


def test_queue_frames_partial_budget():
    # frame de 100MB, 8GB livres → 2GB/100MB = 20 frames
    assert rec._queue_frames(100 * 1024 * 1024, 8 * _GB) == 20


# ── _build_ffmpeg_cmd (estratégia disk e crop) ────────────────────────────────

def test_build_raw_intermediate_uses_rawvideo():
    cmd = rec._build_ffmpeg_cmd(
        "/usr/bin/ffmpeg", 1920, 1080, 144, "/tmp/cap.nut", True,
        raw_intermediate=True,
    )
    assert cmd[cmd.index("-c:v") + 1] == "rawvideo"
    assert "libx264" not in cmd
    assert "+faststart" not in cmd  # nut não usa moov


def test_build_raw_intermediate_ignores_vaapi():
    cmd = rec._build_ffmpeg_cmd(
        "/usr/bin/ffmpeg", 1920, 1080, 60, "/tmp/cap.nut", True,
        vaapi_device="/dev/dri/renderD128", raw_intermediate=True,
    )
    assert "h264_vaapi" not in cmd
    assert "-init_hw_device" not in cmd


def test_build_crop_filter_when_stride_padded():
    cmd = rec._build_ffmpeg_cmd(
        "/usr/bin/ffmpeg", 1928, 1080, 60, "/tmp/out.mp4", True,
        crop=(1920, 1080),
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "crop=1920:1080:0:0" in vf


def test_build_crop_combines_with_vaapi_chain():
    cmd = rec._build_ffmpeg_cmd(
        "/usr/bin/ffmpeg", 1928, 1080, 60, "/tmp/out.mp4", True,
        vaapi_device="/dev/dri/renderD128", crop=(1920, 1080),
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert vf == "crop=1920:1080:0:0,format=nv12,hwupload"


def test_build_no_crop_no_filter_on_cpu():
    cmd = rec._build_ffmpeg_cmd("/usr/bin/ffmpeg", 1920, 1080, 60, "/tmp/out.mp4", True)
    assert "-vf" not in cmd


# ── _build_transcode_cmd ──────────────────────────────────────────────────────

def test_transcode_uses_x264_veryfast():
    cmd = rec._build_transcode_cmd(
        "/usr/bin/ffmpeg", "/tmp/cap.nut", "/tmp/a.mka", "/tmp/out.mp4", True,
    )
    assert "libx264" in cmd
    assert "veryfast" in cmd
    assert cmd[cmd.index("-c:a") + 1] == "copy"
    assert "+faststart" in cmd
    assert cmd[-1] == "/tmp/out.mp4"


def test_transcode_without_audio():
    cmd = rec._build_transcode_cmd(
        "/usr/bin/ffmpeg", "/tmp/cap.nut", None, "/tmp/out.mp4", True,
    )
    assert "-c:a" not in cmd


def test_transcode_falls_back_to_mpeg4():
    cmd = rec._build_transcode_cmd(
        "/usr/bin/ffmpeg", "/tmp/cap.nut", None, "/tmp/out.mp4", False,
    )
    assert "mpeg4" in cmd
    assert "libx264" not in cmd


# ── Deduplicação de frames (encode-on-change) ─────────────────────────────────

def test_is_duplicate_false_for_new_data():
    recorder = rec.ScreenRecorder()
    recorder._last_data = b"aaaa"
    recorder._last_sent_ts = 100.0
    assert recorder._is_duplicate(b"bbbb", 100.1) is False


def test_is_duplicate_true_for_same_data_within_interval():
    recorder = rec.ScreenRecorder()
    recorder._last_data = b"aaaa"
    recorder._last_sent_ts = 100.0
    assert recorder._is_duplicate(b"aaaa", 100.5) is True


def test_is_duplicate_false_after_resend_interval():
    recorder = rec.ScreenRecorder()
    recorder._last_data = b"aaaa"
    recorder._last_sent_ts = 100.0
    assert recorder._is_duplicate(b"aaaa", 101.5) is False


def test_is_duplicate_false_when_no_previous_frame():
    recorder = rec.ScreenRecorder()
    assert recorder._is_duplicate(b"aaaa", 100.0) is False


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
    monkeypatch.setattr(rec, "_pick_ffmpeg", lambda: (None, None, False, False))
    recorder = rec.ScreenRecorder()
    recorder.failed = MagicMock()
    assert recorder.start() is False
    recorder.failed.emit.assert_called_once()
    assert "ffmpeg" in recorder.failed.emit.call_args[0][0].lower()


def test_start_returns_false_when_no_screen(monkeypatch):
    monkeypatch.setattr(rec, "_pick_ffmpeg", lambda: ("/usr/bin/ffmpeg", None, True, False))
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

def _fake_frame(w=1920, h=1080, stride=None):
    frame = MagicMock()
    size = MagicMock()
    size.width.return_value = w
    size.height.return_value = h
    frame.size.return_value = size
    frame.map.return_value = stride is not None
    frame.bytesPerLine.return_value = stride if stride is not None else 0
    frame.pixelFormat.return_value = "Format_RGBA8888"
    return frame


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
    recorder._capture_dest = tmp_path / "out.mp4"
    recorder.failed = MagicMock()
    assert recorder._start_ffmpeg(_fake_frame()) is False
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

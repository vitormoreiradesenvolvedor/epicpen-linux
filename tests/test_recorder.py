"""
Testes do módulo recorder — verifica lógica de detecção e construção de comando
sem dependências de hardware ou de processo externo real.
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
        m = types.ModuleType(name)
        sys.modules.setdefault(name, m)
        return sys.modules[name]

    _mod("PyQt6")
    qtw = _mod("PyQt6.QtWidgets")
    qtc = _mod("PyQt6.QtCore")

    app_mock = MagicMock()
    app_mock.screens.return_value = []
    app_mock.primaryScreen.return_value = None
    qtw.QApplication = MagicMock(return_value=app_mock)
    qtw.QApplication.screens = MagicMock(return_value=[])
    qtw.QApplication.primaryScreen = MagicMock(return_value=None)

    proc_mock = MagicMock()
    proc_mock.ProcessState = MagicMock()
    proc_mock.ProcessState.Running = "Running"
    qtc.QObject  = object
    qtc.QProcess = MagicMock(return_value=proc_mock)
    qtc.QProcess.ProcessState = MagicMock()
    qtc.QProcess.ProcessState.Running = "Running"

    # pyqtSignal stub: retorna objeto com connect/emit
    def _signal(*args, **kwargs):
        s = MagicMock()
        s.connect = MagicMock()
        s.emit    = MagicMock()
        return s
    qtc.pyqtSignal = _signal

_install_qt_stubs()

import recorder as rec  # noqa: E402


# ── Detecção de ambiente ──────────────────────────────────────────────────────

def test_detect_env_x11(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert rec._detect_env() == "x11"


def test_detect_env_xcb_treated_as_x11(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    assert rec._detect_env() == "x11"


def test_detect_env_wayland_wlroots(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "sway")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    assert rec._detect_env() == "wayland-wlroots"


def test_detect_env_wayland_gnome(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    assert rec._detect_env() == "wayland-gnome"


def test_detect_env_wayland_kde(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    assert rec._detect_env() == "wayland-kde"


# ── build_command: X11 ────────────────────────────────────────────────────────

def test_build_x11_no_ffmpeg_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_which", lambda t: None)
    cmd = rec.build_command(tmp_path / "out.mp4", "x11", None, (1920, 1080, 30))
    assert cmd is None


def test_build_x11_cpu_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_which", lambda t: "/usr/bin/ffmpeg" if t == "ffmpeg" else None)
    cmd = rec.build_command(tmp_path / "out.mp4", "x11", None, (1920, 1080, 30))
    assert cmd is not None
    assert "ffmpeg" in cmd[0]
    assert "libx264" in cmd
    assert "ultrafast" in cmd
    assert "30" in cmd          # framerate


def test_build_x11_vaapi(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_which", lambda t: "/usr/bin/ffmpeg" if t == "ffmpeg" else None)
    cmd = rec.build_command(tmp_path / "out.mp4", "x11", "/dev/dri/renderD128", (1920, 1080, 60))
    assert cmd is not None
    assert "h264_vaapi" in cmd
    assert "/dev/dri/renderD128" in cmd


def test_build_x11_resolution_in_command(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_which", lambda t: "/usr/bin/ffmpeg" if t == "ffmpeg" else None)
    cmd = rec.build_command(tmp_path / "out.mp4", "x11", None, (2560, 1440, 144))
    assert "2560x1440" in cmd
    assert "144" in cmd


# ── build_command: Wayland wlroots ────────────────────────────────────────────

def test_build_wlroots_prefers_wl_screenrec_with_vaapi(tmp_path, monkeypatch):
    def _which_mock(t):
        return f"/usr/bin/{t}" if t in ("wl-screenrec", "wf-recorder") else None
    monkeypatch.setattr(rec, "_which", _which_mock)
    cmd = rec.build_command(tmp_path / "out.mp4", "wayland-wlroots", "/dev/dri/renderD128",
                            (1920, 1080, 60))
    assert cmd is not None
    assert "wl-screenrec" in cmd[0]
    assert "/dev/dri/renderD128" in cmd


def test_build_wlroots_falls_back_to_wf_recorder_vaapi(tmp_path, monkeypatch):
    def _which_mock(t):
        return "/usr/bin/wf-recorder" if t == "wf-recorder" else None
    monkeypatch.setattr(rec, "_which", _which_mock)
    cmd = rec.build_command(tmp_path / "out.mp4", "wayland-wlroots", "/dev/dri/renderD128",
                            (1920, 1080, 60))
    assert cmd is not None
    assert "wf-recorder" in cmd[0]
    assert "h264_vaapi" in cmd


def test_build_wlroots_cpu_fallback(tmp_path, monkeypatch):
    def _which_mock(t):
        return "/usr/bin/wf-recorder" if t == "wf-recorder" else None
    monkeypatch.setattr(rec, "_which", _which_mock)
    cmd = rec.build_command(tmp_path / "out.mp4", "wayland-wlroots", None, (1920, 1080, 60))
    assert cmd is not None
    assert "wf-recorder" in cmd[0]
    assert "libx264" in cmd


def test_build_wlroots_no_tools_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_which", lambda t: None)
    cmd = rec.build_command(tmp_path / "out.mp4", "wayland-wlroots", None, (1920, 1080, 60))
    assert cmd is None


# ── build_command: GNOME/KDE Wayland ─────────────────────────────────────────

def test_build_gnome_prefers_gpu_screen_recorder(tmp_path, monkeypatch):
    def _which_mock(t):
        return f"/usr/bin/{t}" if t in ("gpu-screen-recorder", "wf-recorder") else None
    monkeypatch.setattr(rec, "_which", _which_mock)
    cmd = rec.build_command(tmp_path / "out.mp4", "wayland-gnome", None, (1920, 1080, 60))
    assert cmd is not None
    assert "gpu-screen-recorder" in cmd[0]


def test_build_kde_falls_back_to_wf_recorder(tmp_path, monkeypatch):
    def _which_mock(t):
        return "/usr/bin/wf-recorder" if t == "wf-recorder" else None
    monkeypatch.setattr(rec, "_which", _which_mock)
    cmd = rec.build_command(tmp_path / "out.mp4", "wayland-kde", None, (1920, 1080, 60))
    assert cmd is not None
    assert "wf-recorder" in cmd[0]


def test_build_gnome_no_tools_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_which", lambda t: None)
    cmd = rec.build_command(tmp_path / "out.mp4", "wayland-gnome", None, (1920, 1080, 60))
    assert cmd is None


# ── Dest path ─────────────────────────────────────────────────────────────────

def test_dest_path_in_command(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_which", lambda t: "/usr/bin/ffmpeg" if t == "ffmpeg" else None)
    dest = tmp_path / "out.mp4"
    cmd = rec.build_command(dest, "x11", None, (1920, 1080, 30))
    assert str(dest) in cmd

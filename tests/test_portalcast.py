"""Testes do roteamento do portal (portalcast) e da deduplicação do helper.

portalcast só importa stdlib no topo (o gi/Gst é carregado sob demanda em
available()), então needs_portal/helper_cmd testam sem GStreamer. O teste da
deduplicação pula se PyGObject/Gst não estiver presente no ambiente.
"""
import json

import pytest

import portalcast


def test_needs_portal_monitor_cursor_silent_on_x11(monkeypatch):
    # X11: QScreenCapture embute o cursor → caminho silencioso, sem portal.
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert portalcast.needs_portal("monitor", True) is False


def test_needs_portal_monitor_cursor_uses_portal_on_wayland(monkeypatch):
    # Wayland: QScreenCapture NÃO embute o cursor → portal (cursor EMBEDDED).
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert portalcast.needs_portal("monitor", True) is True


def test_needs_portal_window_always():
    assert portalcast.needs_portal("window", True) is True
    assert portalcast.needs_portal("window", False) is True


def test_needs_portal_hide_cursor_forces_portal():
    assert portalcast.needs_portal("monitor", False) is True


def test_helper_cmd_encodes_options():
    cmd = portalcast.helper_cmd("window", False, None)
    opts = json.loads(cmd[-1])
    assert opts["source"] == "window"
    assert opts["cursor"] is False
    assert "restore_token" not in opts
    assert cmd[-2].endswith("portal_capture_helper.py")


def test_helper_cmd_includes_restore_token():
    cmd = portalcast.helper_cmd("monitor", True, "tok-42")
    opts = json.loads(cmd[-1])
    assert opts["restore_token"] == "tok-42"
    assert opts["cursor"] is True


def test_should_send_dedup():
    pytest.importorskip("gi")
    import gi
    try:
        gi.require_version("Gst", "1.0")
    except ValueError:
        pytest.skip("GStreamer 1.0 ausente")
    from portal_capture_helper import should_send

    a = b"x" * 64
    # Primeiro frame (sem anterior) sempre desce.
    assert should_send(a, None, 0.0, 0.0) is True
    # Igual e dentro do intervalo de reenvio: descartado.
    assert should_send(a, a, 0.5, 0.0) is False
    # Igual mas passou o intervalo: reenvia (limita corte de cauda).
    assert should_send(a, a, 1.5, 0.0) is True
    # Mudou: desce mesmo dentro do intervalo.
    assert should_send(b"y" * 64, a, 0.1, 0.0) is True

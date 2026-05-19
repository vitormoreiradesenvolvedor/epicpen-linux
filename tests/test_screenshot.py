"""
Testes do módulo screenshot — verifica lógica de path/filename sem captura real.
"""
import sys
import re
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

# Stubs Qt mínimos para importar screenshot.py sem PyQt6 instalado
def _install_qt_stubs():
    def _mod(name):
        m = types.ModuleType(name)
        sys.modules.setdefault(name, m)
        return sys.modules[name]

    _mod("PyQt6")
    qtw = _mod("PyQt6.QtWidgets")
    qtc = _mod("PyQt6.QtCore")
    qtg = _mod("PyQt6.QtGui")

    qtw.QApplication = MagicMock()
    qtc.QTimer       = MagicMock()
    qtg.QClipboard   = MagicMock()

_install_qt_stubs()

import screenshot as sc  # noqa: E402


# ── Testes ────────────────────────────────────────────────────────────────────

def test_save_dir_name():
    assert sc._SAVE_DIR.name == "EpicPen"
    assert sc._SAVE_DIR.parts[-2] == "Imagens"


def test_capture_hides_toolbar_before_singleshot():
    """hide() deve ser chamado ANTES de QTimer.singleShot."""
    order = []
    toolbar = MagicMock()
    toolbar.hide.side_effect = lambda: order.append("hide")

    with patch.object(sc, "QTimer") as mock_timer:
        mock_timer.singleShot.side_effect = lambda ms, fn: order.append("timer")
        sc.capture(toolbar)

    assert order == ["hide", "timer"]


def test_capture_schedules_80ms_delay():
    toolbar = MagicMock()
    with patch.object(sc, "QTimer") as mock_timer:
        sc.capture(toolbar)
        delay = mock_timer.singleShot.call_args[0][0]
    assert delay == 80


def test_do_capture_saves_to_save_dir(tmp_path):
    """O closure _do_capture deve salvar em _SAVE_DIR com nome correto."""
    saved_paths = []

    mock_pixmap = MagicMock()
    mock_pixmap.save.side_effect = lambda p: saved_paths.append(Path(p))

    mock_screen = MagicMock()
    mock_screen.grabWindow.return_value = mock_pixmap

    toolbar = MagicMock()

    original_dir = sc._SAVE_DIR
    sc._SAVE_DIR = tmp_path / "EpicPen"

    try:
        captured = {}

        with patch.object(sc, "QTimer") as mock_timer:
            mock_timer.singleShot.side_effect = lambda ms, fn: captured.update({"fn": fn})
            with patch.object(sc, "QApplication") as mock_app:
                mock_app.primaryScreen.return_value = mock_screen
                sc.capture(toolbar)
                captured["fn"]()   # executa o closure imediatamente
    finally:
        sc._SAVE_DIR = original_dir

    assert len(saved_paths) == 1
    assert re.match(r"epicpen_\d{8}_\d{6}\.png", saved_paths[0].name)


def test_do_capture_restores_toolbar(tmp_path):
    """Após capturar, toolbar.show() deve ser chamado."""
    mock_pixmap = MagicMock()
    mock_pixmap.save = MagicMock()
    mock_screen = MagicMock()
    mock_screen.grabWindow.return_value = mock_pixmap
    toolbar = MagicMock()

    original_dir = sc._SAVE_DIR
    sc._SAVE_DIR = tmp_path / "EpicPen"

    try:
        captured = {}
        with patch.object(sc, "QTimer") as mock_timer:
            mock_timer.singleShot.side_effect = lambda ms, fn: captured.update({"fn": fn})
            with patch.object(sc, "QApplication") as mock_app:
                mock_app.primaryScreen.return_value = mock_screen
                sc.capture(toolbar)
                captured["fn"]()
    finally:
        sc._SAVE_DIR = original_dir

    toolbar.show.assert_called_once()


def test_do_capture_notifies_tray(tmp_path):
    """Se tray_icon fornecido, showMessage deve ser chamado após captura."""
    mock_pixmap = MagicMock()
    mock_pixmap.save = MagicMock()
    mock_screen = MagicMock()
    mock_screen.grabWindow.return_value = mock_pixmap
    toolbar = MagicMock()
    tray = MagicMock()

    original_dir = sc._SAVE_DIR
    sc._SAVE_DIR = tmp_path / "EpicPen"

    try:
        captured = {}
        with patch.object(sc, "QTimer") as mock_timer:
            mock_timer.singleShot.side_effect = lambda ms, fn: captured.update({"fn": fn})
            with patch.object(sc, "QApplication") as mock_app:
                mock_app.primaryScreen.return_value = mock_screen
                sc.capture(toolbar, tray_icon=tray)
                captured["fn"]()
    finally:
        sc._SAVE_DIR = original_dir

    tray.showMessage.assert_called_once()
    title, msg, *_ = tray.showMessage.call_args[0]
    assert "EpicPen" in title

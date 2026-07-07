"""Testes de screens.py — validação de QScreen contra ponteiros pendentes.

Sem display real: stub mínimo de PyQt6.QtWidgets.QApplication, igual ao padrão
de test_recorder. O ponto central é que um QScreen removido (wrapper C++
deletado) levanta RuntimeError ao ser comparado — screen_alive tem de tratar.
"""
import sys
import types
from unittest.mock import MagicMock


def _install_qt_stubs():
    if "PyQt6" not in sys.modules:
        sys.modules["PyQt6"] = types.ModuleType("PyQt6")
    if "PyQt6.QtWidgets" not in sys.modules:
        sys.modules["PyQt6.QtWidgets"] = types.ModuleType("PyQt6.QtWidgets")
    sys.modules["PyQt6.QtWidgets"].QApplication = MagicMock()


_install_qt_stubs()

import screens  # noqa: E402


def _set_screens(screen_list, primary=None):
    from PyQt6.QtWidgets import QApplication
    QApplication.screens = MagicMock(return_value=screen_list)
    QApplication.primaryScreen = MagicMock(return_value=primary)


def test_screen_alive_none_is_false():
    _set_screens([])
    assert screens.screen_alive(None) is False


def test_screen_alive_true_when_connected():
    s = object()
    _set_screens([s])
    assert screens.screen_alive(s) is True


def test_screen_alive_false_when_removed():
    s = object()
    _set_screens([object()])   # outro monitor; s já não está na lista
    assert screens.screen_alive(s) is False


def test_screen_alive_handles_deleted_wrapper():
    """QScreen cujo wrapper C++ foi deletado levanta RuntimeError no `in`."""
    class _Dead:
        def __eq__(self, other):
            raise RuntimeError("wrapped C/C++ object of type QScreen has been deleted")
        __hash__ = None

    class _ListRaises(list):
        def __contains__(self, item):
            raise RuntimeError("deleted")

    from PyQt6.QtWidgets import QApplication
    QApplication.screens = MagicMock(return_value=_ListRaises([object()]))
    assert screens.screen_alive(_Dead()) is False


def test_safe_screen_returns_live_screen():
    s = object()
    _set_screens([s], primary=s)
    assert screens.safe_screen(s) is s


def test_safe_screen_falls_back_to_primary():
    dead = object()
    primary = object()
    _set_screens([primary], primary=primary)   # dead não está na lista
    assert screens.safe_screen(dead) is primary


def test_safe_screen_none_when_no_screens():
    _set_screens([], primary=None)
    assert screens.safe_screen(object()) is None

"""
Testes de lógica pura do OverlayWindow sem dependência de display/PyQt6.
Todos os tipos Qt são substituídos por simples namespaces.
"""
import sys
import types
import pytest
from unittest.mock import MagicMock, patch


# ── Stubs mínimos de Qt ────────────────────────────────────────────────────────

class _QPoint:
    def __init__(self, x=0, y=0):
        self.x_val, self.y_val = x, y
    def x(self): return self.x_val
    def y(self): return self.y_val
    def __sub__(self, other):
        return _QPoint(self.x_val - other.x_val, self.y_val - other.y_val)


class _QPointF(_QPoint):
    """Stub de QPointF com suporte a += (usado em _wb_pan)."""
    def __init__(self, x=0.0, y=0.0):
        if isinstance(x, (_QPoint, _QPointF)):
            self.x_val, self.y_val = float(x.x_val), float(x.y_val)
        else:
            self.x_val, self.y_val = float(x), float(y)
    def __iadd__(self, other):
        if isinstance(other, (_QPoint, _QPointF)):
            self.x_val += other.x_val
            self.y_val += other.y_val
        return self


class _QColor:
    def __init__(self, s="#000000"):
        if isinstance(s, _QColor):
            self._name = s._name
        else:
            self._name = s.lower() if isinstance(s, str) else "#000000"
    def name(self): return self._name


class _QPixmap:
    def __init__(self, *a): pass
    def fill(self, *a): pass
    def size(self): return MagicMock()
    def isNull(self): return False


class _QPainter:
    """Stub de QPainter — aceita qualquer chamada de método sem efeitos."""
    RenderHint      = MagicMock()
    CompositionMode = MagicMock()
    def __init__(self, *a): pass
    def __getattr__(self, _): return lambda *a, **kw: None
    def end(self): pass


class _QWidget:
    """Stub mínimo de QWidget que não interfere com __new__ nem __setattr__."""
    def __init__(self, *a, **kw): pass
    def setGeometry(self, *a): pass
    def setWindowFlags(self, *a): pass
    def setAttribute(self, *a): pass
    def setCursor(self, *a): pass
    def setMouseTracking(self, *a): pass
    def update(self, *a): pass
    def show(self): pass
    def hide(self): pass
    def raise_(self): pass
    def isVisible(self): return True
    def windowHandle(self): return MagicMock()
    def rect(self): return MagicMock()
    def size(self): return MagicMock()


class _QTimer:
    """Stub mínimo de QTimer."""
    def __init__(self, *a, **kw): pass
    def setInterval(self, *a): pass
    def timeout(self): pass
    def start(self, *a): pass
    def stop(self): pass
    # permite .timeout.connect(fn) sem erro
    class _Signal:
        def connect(self, *a): pass
    timeout = _Signal()


def _make_qt_stubs():
    """Injeta módulos Qt falsos no sys.modules antes de importar overlay."""
    def _mod(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    _mod("PyQt6")
    qtwidgets = _mod("PyQt6.QtWidgets")
    qtcore    = _mod("PyQt6.QtCore")
    qtgui     = _mod("PyQt6.QtGui")

    # QWidget precisa ser uma classe real para herança funcionar
    qtwidgets.QWidget      = _QWidget
    qtwidgets.QApplication = MagicMock

    # QtCore
    qtcore.Qt      = MagicMock()
    qtcore.QPoint  = _QPoint
    qtcore.QPointF = _QPointF
    qtcore.QRect   = MagicMock
    qtcore.QRectF  = MagicMock
    qtcore.QTimer  = _QTimer
    # pyqtSignal stub: classe que ignora o tipo e age como descritor no-op
    class _Signal:
        def __init__(self, *a): pass
        def connect(self, *a): pass
        def emit(self, *a): pass
        def __get__(self, obj, cls): return self
    qtcore.pyqtSignal = lambda *a: _Signal()

    # QtGui
    qtgui.QPainter        = _QPainter
    qtgui.QPen            = MagicMock
    qtgui.QColor          = _QColor
    qtgui.QScreen         = MagicMock
    qtgui.QPainterPath    = MagicMock
    qtgui.QRadialGradient = MagicMock
    qtgui.QBrush          = MagicMock
    qtgui.QPixmap         = _QPixmap
    qtgui.QFont           = MagicMock


_make_qt_stubs()

# Stub do módulo cursors para que overlay.py possa importá-lo
import types as _types
_cursors_mod = _types.ModuleType("cursors")
_cursors_mod.make_pen_cursor       = MagicMock(return_value=MagicMock())
_cursors_mod.make_eraser_cursor    = MagicMock(return_value=MagicMock())
_cursors_mod.make_crosshair_cursor = MagicMock(return_value=MagicMock())
sys.modules["cursors"] = _cursors_mod

# Agora podemos importar overlay sem PyQt6 real
from overlay import OverlayWindow   # noqa: E402  (import após stubs)


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def overlay():
    ov = OverlayWindow.__new__(OverlayWindow)
    ov._strokes        = []
    ov._current_stroke = []
    ov._undo_stack     = []
    ov._tool           = "pen"
    ov._color          = _QColor("#FF0000")
    ov._size           = 3
    ov._drawing        = False
    ov._active         = True
    ov._laser_pos      = None
    ov._laser_trail    = []
    ov._whiteboard     = False
    ov._spotlight      = False
    ov._spotlight_pos  = None
    ov._spotlight_radius = 150
    ov._canvas           = None
    ov._erase_scratch    = None
    ov._wb_pan           = _QPointF(0.0, 0.0)
    ov._wb_zoom          = 1.0
    ov._wb_bg            = _QColor("#ffffff")
    ov._wb_panning       = False
    ov._wb_pan_start_mouse = None
    ov._wb_pan_start_val   = None
    ov.update            = MagicMock()
    ov._update_tracking  = MagicMock()
    return ov


# ── Testes ────────────────────────────────────────────────────────────────────

def test_initial_state(overlay):
    assert overlay._tool == "pen"
    assert overlay._whiteboard is False
    assert overlay._spotlight is False
    assert overlay._strokes == []


def test_undo_redo_on_empty_does_not_raise(overlay):
    overlay.undo()
    overlay.redo()
    assert overlay._strokes == []


def test_undo_moves_stroke_to_undo_stack(overlay):
    stroke = [(_QPoint(0, 0), {"tool": "pen", "color": None, "size": 3})]
    overlay._strokes.append(stroke)
    overlay.undo()
    assert overlay._strokes == []
    assert len(overlay._undo_stack) == 1


def test_redo_restores_stroke(overlay):
    stroke = [(_QPoint(0, 0), {"tool": "pen", "color": None, "size": 3})]
    overlay._strokes.append(stroke)
    overlay.undo()
    overlay.redo()
    assert len(overlay._strokes) == 1
    assert overlay._undo_stack == []


def test_undo_clears_undo_stack_on_new_draw(overlay):
    """Após um novo traço, redo não deve repor traços anteriores (stack limpo)."""
    stroke = [(_QPoint(0, 0), {"tool": "pen", "color": None, "size": 3})]
    overlay._strokes.append(stroke)
    overlay.undo()
    assert len(overlay._undo_stack) == 1
    # Simula novo traço (mousePressEvent limpa undo_stack)
    overlay._undo_stack.clear()
    overlay.redo()   # sem nada para refazer
    assert overlay._strokes == []


def test_clear_resets_strokes_and_undo_stack(overlay):
    stroke = [(_QPoint(1, 1), {"tool": "pen", "color": None, "size": 3})]
    overlay._strokes.append(stroke)
    overlay._undo_stack.append(stroke)
    overlay.clear()
    assert overlay._strokes == []
    assert overlay._undo_stack == []


def test_set_whiteboard_toggles_flag(overlay):
    overlay.set_whiteboard(True)
    assert overlay._whiteboard is True
    overlay.set_whiteboard(False)
    assert overlay._whiteboard is False


def test_set_spotlight_activates_and_clears_pos(overlay):
    overlay._spotlight_pos = _QPoint(100, 100)
    overlay.set_spotlight(True)
    assert overlay._spotlight is True
    overlay.set_spotlight(False)
    assert overlay._spotlight is False
    assert overlay._spotlight_pos is None


def test_set_spotlight_radius(overlay):
    overlay.set_spotlight_radius(300)
    assert overlay._spotlight_radius == 300


def test_brush_props_copies_color(overlay):
    overlay._color = _QColor("#00FF00")
    props = overlay._brush_props()
    overlay._color = _QColor("#0000FF")
    assert props["color"].name() == "#00ff00"


def test_brush_props_contains_tool_and_size(overlay):
    overlay._tool = "highlighter"
    overlay._size = 7
    props = overlay._brush_props()
    assert props["tool"] == "highlighter"
    assert props["size"] == 7


def test_set_whiteboard_resets_pan(overlay):
    overlay._wb_pan = _QPointF(100.0, 200.0)
    overlay.set_whiteboard(True)
    overlay.set_whiteboard(False)
    assert overlay._wb_pan.x_val == 0.0
    assert overlay._wb_pan.y_val == 0.0


def test_set_whiteboard_bg_updates_color(overlay):
    overlay._wb_bg = _QColor("#ffffff")
    overlay.set_whiteboard_bg(_QColor("#aabbcc"))
    assert overlay._wb_bg.name() == "#aabbcc"


def test_to_canvas_no_offset_when_pan_zero(overlay):
    pos = _QPoint(100, 200)
    result = overlay._to_canvas(pos)
    assert result.x() == 100
    assert result.y() == 200


def test_to_canvas_subtracts_pan_in_whiteboard(overlay):
    overlay._whiteboard = True
    overlay._wb_pan = _QPointF(30.0, 50.0)
    pos = _QPoint(130, 250)
    result = overlay._to_canvas(pos)
    assert result.x() == 100
    assert result.y() == 200


def test_redo_does_not_use_canvas_in_whiteboard(overlay):
    stroke = [(_QPoint(0, 0), {"tool": "pen", "color": None, "size": 3})]
    overlay._strokes.append(stroke)
    overlay.undo()
    overlay._whiteboard = True
    overlay.redo()
    assert len(overlay._strokes) == 1
    # Em modo whiteboard, _canvas não deve ser criado pelo redo
    assert overlay._canvas is None

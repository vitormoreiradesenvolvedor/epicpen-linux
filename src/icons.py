"""
Todos os ícones da toolbar desenhados com QPainter (sem emoji / sem fontes externas).
Cada função retorna QIcon pronto para uso em QPushButton.setIcon().
"""
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QPainterPath, QBrush
from PyQt6.QtCore import Qt, QRectF, QPointF

_SIZE = 22
_W = QColor(230, 230, 230)   # branco sujo — contraste bom no fundo escuro
_R = QColor(220, 70, 70)     # vermelho para acentos


def _make(draw_fn) -> QIcon:
    px = QPixmap(_SIZE, _SIZE)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_fn(p)
    p.end()
    return QIcon(px)


# ── Ferramentas ───────────────────────────────────────────────────────────────

def pen(accent: QColor | None = None) -> QIcon:
    c = accent or _R
    def draw(p):
        body = QPainterPath()
        body.moveTo(4, 18); body.lineTo(6, 20); body.lineTo(19, 5); body.lineTo(17, 3)
        body.closeSubpath()
        p.setPen(QPen(_W, 0.8)); p.setBrush(QBrush(_W)); p.drawPath(body)
        tip = QPainterPath()
        tip.moveTo(4, 18); tip.lineTo(6, 20); tip.lineTo(2, 21)
        tip.closeSubpath()
        p.setBrush(QBrush(c)); p.drawPath(tip)
    return _make(draw)


def highlighter() -> QIcon:
    def draw(p):
        hi = QColor(255, 230, 50, 180)
        p.setPen(QPen(hi, 6, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(4, 18), QPointF(18, 4))
        p.setPen(QPen(_W, 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(4, 18), QPointF(18, 4))
    return _make(draw)


def line() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(3, 19), QPointF(19, 3))
    return _make(draw)


def rect() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 1.8)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(3, 5, 16, 12), 1, 1)
    return _make(draw)


def circle() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 1.8)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(2, 3, 18, 16))
    return _make(draw)


def eraser() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 1.5)); p.setBrush(QBrush(QColor(200, 200, 200, 80)))
        p.drawRoundedRect(QRectF(3, 7, 16, 10), 2, 2)
        p.setPen(QPen(_W, 1))
        for x in (7, 11, 15):
            p.drawLine(QPointF(x, 7), QPointF(x, 17))
    return _make(draw)


def laser() -> QIcon:
    def draw(p):
        cx, cy = 11.0, 11.0
        # Brilho
        for r in (8, 5, 3):
            c = QColor(255, 60, 60, max(0, 160 - r * 18))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(c))
            p.drawEllipse(QPointF(cx, cy), float(r), float(r))
        # Núcleo branco
        p.setBrush(QBrush(QColor(255, 255, 255, 240)))
        p.drawEllipse(QPointF(cx, cy), 2.5, 2.5)
        # Raios
        p.setPen(QPen(QColor(255, 80, 80, 140), 1))
        for angle_pts in [(cx, 2), (cx, 20), (2, cy), (20, cy),
                          (4, 4), (18, 18), (18, 4), (4, 18)]:
            ox = cx + (angle_pts[0] - cx) * 0.45
            oy = cy + (angle_pts[1] - cy) * 0.45
            p.drawLine(QPointF(ox, oy), QPointF(*angle_pts))
    return _make(draw)


# ── Ações ─────────────────────────────────────────────────────────────────────

def undo() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath()
        path.moveTo(16, 7); path.arcTo(QRectF(5, 7, 12, 10), 60, 200)
        p.drawPath(path)
        # Ponta da seta
        p.setBrush(QBrush(_W)); p.setPen(Qt.PenStyle.NoPen)
        arr = QPainterPath()
        arr.moveTo(5, 6); arr.lineTo(9, 6); arr.lineTo(7, 10)
        arr.closeSubpath(); p.drawPath(arr)
    return _make(draw)


def redo() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath()
        path.moveTo(6, 7); path.arcTo(QRectF(5, 7, 12, 10), 120, -200)
        p.drawPath(path)
        p.setBrush(QBrush(_W)); p.setPen(Qt.PenStyle.NoPen)
        arr = QPainterPath()
        arr.moveTo(17, 6); arr.lineTo(13, 6); arr.lineTo(15, 10)
        arr.closeSubpath(); p.drawPath(arr)
    return _make(draw)


def trash() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
        # Tampa
        p.drawLine(QPointF(4, 7), QPointF(18, 7))
        p.drawRoundedRect(QRectF(7, 4, 8, 3), 1, 1)
        # Corpo
        p.drawRoundedRect(QRectF(5, 8, 12, 11), 1, 1)
        # Linhas internas
        p.setPen(QPen(_W, 1))
        for x in (8, 11, 14):
            p.drawLine(QPointF(x, 10), QPointF(x, 17))
    return _make(draw)


def screenshot() -> QIcon:
    def draw(p):
        # Corpo da câmera
        p.setPen(QPen(_W, 1.5)); p.setBrush(QBrush(QColor(200, 200, 200, 40)))
        p.drawRoundedRect(QRectF(2, 7, 18, 12), 2, 2)
        # Abertura do obturador
        p.drawRoundedRect(QRectF(7, 4, 8, 4), 1, 1)
        # Lente
        p.setBrush(QBrush(QColor(120, 180, 255, 180)))
        p.drawEllipse(QPointF(11, 13), 3.5, 3.5)
        p.setPen(QPen(_W, 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(11, 13), 3.5, 3.5)
    return _make(draw)


# ── Modos ─────────────────────────────────────────────────────────────────────

def whiteboard() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 1.5))
        p.setBrush(QBrush(QColor(255, 255, 255, 200)))
        p.drawRoundedRect(QRectF(2, 3, 18, 14), 2, 2)
        # Pés do quadro
        p.setPen(QPen(_W, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(8, 17), QPointF(6, 20))
        p.drawLine(QPointF(14, 17), QPointF(16, 20))
        p.drawLine(QPointF(8, 17), QPointF(14, 17))
    return _make(draw)


def spotlight() -> QIcon:
    def draw(p):
        # Feixe
        beam = QPainterPath()
        beam.moveTo(5, 3); beam.lineTo(17, 3); beam.lineTo(19, 19); beam.lineTo(3, 19)
        beam.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 230, 80, 100)))
        p.drawPath(beam)
        # Borda do feixe
        p.setPen(QPen(_W, 1.2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(beam)
        # Fonte de luz (pequeno arco no topo)
        p.setBrush(QBrush(_W))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(7, 1, 8, 4))
    return _make(draw)


def magnifier() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 2)); p.setBrush(QBrush(QColor(180, 210, 255, 60)))
        p.drawEllipse(QRectF(2, 2, 14, 14))
        p.setPen(QPen(_W, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(14, 14), QPointF(20, 20))
        # Mira
        p.setPen(QPen(QColor(255, 80, 80, 180), 1))
        p.drawLine(QPointF(5, 9), QPointF(7.5, 9))
        p.drawLine(QPointF(10.5, 9), QPointF(13, 9))
        p.drawLine(QPointF(9, 5), QPointF(9, 7.5))
        p.drawLine(QPointF(9, 10.5), QPointF(9, 13))
    return _make(draw)


def presentation() -> QIcon:
    def draw(p):
        # Tela
        p.setPen(QPen(_W, 1.5)); p.setBrush(QBrush(QColor(200, 200, 200, 40)))
        p.drawRoundedRect(QRectF(1, 3, 20, 14), 2, 2)
        # Ícone de "esconder" (seta para a esquerda)
        p.setBrush(QBrush(_W)); p.setPen(Qt.PenStyle.NoPen)
        arr = QPainterPath()
        arr.moveTo(7, 10); arr.lineTo(12, 7); arr.lineTo(12, 13)
        arr.closeSubpath(); p.drawPath(arr)
        # Barra de presença na barra lateral
        p.setPen(QPen(QColor(100, 200, 100, 200), 1.5))
        p.drawLine(QPointF(15, 7), QPointF(18, 7))
        p.drawLine(QPointF(15, 10), QPointF(18, 10))
        p.drawLine(QPointF(15, 13), QPointF(18, 13))
    return _make(draw)


def mouse_pause() -> QIcon:
    def draw(p):
        # Cursor seta
        path = QPainterPath()
        path.moveTo(4, 3); path.lineTo(4, 17); path.lineTo(8, 13)
        path.lineTo(11, 19); path.lineTo(13, 18); path.lineTo(10, 12)
        path.lineTo(15, 12); path.closeSubpath()
        p.setPen(QPen(QColor(0, 0, 0, 100), 1))
        p.setBrush(QBrush(_W)); p.drawPath(path)
    return _make(draw)


def mouse_active() -> QIcon:
    def draw(p):
        path = QPainterPath()
        path.moveTo(4, 3); path.lineTo(4, 17); path.lineTo(8, 13)
        path.lineTo(11, 19); path.lineTo(13, 18); path.lineTo(10, 12)
        path.lineTo(15, 12); path.closeSubpath()
        p.setPen(QPen(QColor(80, 180, 80, 200), 1.5))
        p.setBrush(QBrush(QColor(100, 220, 100, 200))); p.drawPath(path)
    return _make(draw)


def color_dot(color: QColor) -> QIcon:
    def draw(p):
        p.setPen(QPen(QColor(255, 255, 255, 120), 1.5))
        p.setBrush(QBrush(color))
        p.drawEllipse(QRectF(3, 3, 16, 16))
        # Brilho
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 60)))
        p.drawEllipse(QRectF(5, 5, 6, 6))
    return _make(draw)


def collapse_left() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(13, 5), QPointF(7, 11))
        p.drawLine(QPointF(7, 11), QPointF(13, 17))
    return _make(draw)


def expand_right() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(9, 5), QPointF(15, 11))
        p.drawLine(QPointF(15, 11), QPointF(9, 17))
    return _make(draw)


def drag_handle() -> QIcon:
    def draw(p):
        p.setPen(QPen(QColor(180, 180, 180, 140), 1.5,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for y in (7, 11, 15):
            p.drawLine(QPointF(6, float(y)), QPointF(16, float(y)))
    return _make(draw)


def text_tool() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(5, 5), QPointF(17, 5))
        p.drawLine(QPointF(11, 5), QPointF(11, 19))
    return _make(draw)


def drag_tool() -> QIcon:
    def draw(p):
        p.setPen(QPen(_W, 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for pts in [
            [(11, 2), (9, 5), (13, 5)],
            [(11, 20), (9, 17), (13, 17)],
            [(2, 11), (5, 9), (5, 13)],
            [(20, 11), (17, 9), (17, 13)],
        ]:
            path = QPainterPath()
            path.moveTo(*pts[0])
            path.lineTo(*pts[1])
            path.lineTo(*pts[2])
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_W))
            p.drawPath(path)
        p.setPen(QPen(_W, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(11, 4), QPointF(11, 18))
        p.drawLine(QPointF(4, 11), QPointF(18, 11))
    return _make(draw)


def exit_btn(hover: bool = False) -> QIcon:
    c = _R if hover else _W
    def draw(p):
        p.setPen(QPen(c, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Arco 280° com abertura de 80° centrada no topo (90°): de 130° a 50° CCW
        p.drawArc(QRectF(3, 4, 16, 16), 130 * 16, 280 * 16)
        # Linha vertical pelo centro da abertura
        p.drawLine(QPointF(11, 2), QPointF(11, 11))
    return _make(draw)



def record() -> QIcon:
    def draw(p):
        # Círculo vermelho sólido — ícone universal de "gravar"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(220, 60, 60)))
        p.drawEllipse(QRectF(4, 4, 14, 14))
        # Anel externo branco tênue
        p.setPen(QPen(QColor(230, 230, 230, 120), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(2, 2, 18, 18))
    return _make(draw)


def record_stop() -> QIcon:
    def draw(p):
        # Quadrado branco sólido — ícone universal de "parar"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(230, 230, 230)))
        p.drawRoundedRect(QRectF(5, 5, 12, 12), 1.5, 1.5)
    return _make(draw)


def logo() -> QIcon:
    """Ícone para o botão colapsado — mesmo PNG usado pela tray."""
    from pathlib import Path
    png = Path(__file__).parent.parent / "resources" / "icons" / "epicpen.png"
    if png.exists():
        return QIcon(str(png))
    # Fallback idêntico ao tray: círculo escuro 64×64 + "EP" vermelho
    from PyQt6.QtGui import QFont
    px = QPixmap(64, 64)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(30, 30, 30, 230)))
    p.drawEllipse(QRectF(2, 2, 60, 60))
    p.setPen(QPen(QColor(200, 200, 200, 120), 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(2, 2, 60, 60))
    f = QFont("Sans Serif", 20, QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(QPen(QColor(220, 60, 60)))
    p.drawText(QRectF(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "EP")
    p.end()
    return QIcon(px)

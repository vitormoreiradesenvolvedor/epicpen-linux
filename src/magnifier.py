import time
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath, QBrush, QCursor, QPixmap, QFont

from screenshot import grab_region, region_backend, _IS_WAYLAND

DIAMETER = 220


class MagnifierWindow(QWidget):
    """Lupa circular flutuante que amplifica a região ao redor do cursor."""

    def __init__(self):
        super().__init__()
        self._zoom   = 3
        self._cursor_pos = QPoint(0, 0)
        self._last_px: QPixmap | None = None
        self._unavailable = False   # True se nenhum método de captura funcionar
        # Fallback Wayland sem ferramenta externa: captura ao vivo via
        # portal de ScreenCast (LiveFrameSource) — bundled no AppImage
        self._live = None
        self._use_live = False
        self._live_deadline = 0.0   # limite para o 1º frame do helper
        self._live_probe = 0        # ticks até re-sondar o backend do KWin
        # Wayland: janela comum não se move sozinha (move() é ignorado) —
        # com layer-shell o reposicionamento vira move_to(). O apply é
        # adiado para a 1ª ativação: criar a superfície no arranque sem
        # nunca mostrá-la desestabilizava o Qt Wayland (segfault posterior
        # em activateWindow na toolbar).
        self._lsw_ptr = None
        self._ls_screen = None
        self._ls_wanted = False
        # Cursor vindo dos eventos do overlay (QCursor.pos() global não é
        # confiável em superfícies layer-shell)
        self._ext_pos: QPoint | None = None
        # Rastreio global no KDE: script no KWin envia workspace.cursorPos
        # por DBus — a lupa segue a seta mesmo fora do overlay
        self._tracker = None
        # Retângulo a evitar (a toolbar): a lupa nunca se posiciona sobre
        # ele e some quando a região capturada o alcançaria
        self._avoid = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            # Input region vazia no wl_surface (nativo do Qt Wayland):
            # cliques atravessam a lupa. NÃO usar set_empty_input_region
            # do layershell.py — o caminho ctypes segfaulta.
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(DIAMETER, DIAMETER)

        # Wayland: grim tem latência maior, reduz para ~15fps para não travar
        interval = 66 if _IS_WAYLAND else 16
        self._timer = QTimer(self)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self._tick)

        self.hide()

    # ── Public API ────────────────────────────────────────────────────────

    def set_avoid_provider(self, fn):
        """fn() → QRect absoluto que a lupa deve evitar (a toolbar)."""
        self._avoid = fn

    def enable_layershell(self, screen):
        """Wayland com wlr-layer-shell: a lupa vira surface layer-shell na
        primeira ativação, no monitor do overlay/toolbar."""
        self._ls_screen = screen
        self._ls_wanted = True

    def on_overlay_cursor(self, local: QPoint):
        """Posição do cursor via eventos do overlay (coords locais)."""
        if self._ls_screen is not None:
            self._ext_pos = local + self._ls_screen.geometry().topLeft()

    def change_screen(self, screen):
        """Migra o surface layer-shell para outro monitor (hide+show
        recria a wl_layer_surface no novo output — padrão da toolbar)."""
        if not self._ls_wanted and self._lsw_ptr is None:
            return
        self._ls_screen = screen
        self._ext_pos = None
        if self._lsw_ptr is None:
            return  # ainda sem surface — a 1ª ativação já usa o novo monitor
        wh = self.windowHandle()
        if wh:
            wh.setScreen(screen)
        if self.isVisible():
            self.hide()
            self.show()

    def set_zoom(self, zoom: int):
        self._zoom = max(2, min(zoom, 6))

    def set_active(self, active: bool):
        if not active:
            self._timer.stop()
            self.hide()
            if self._live is not None:
                self._live.stop()
            if self._tracker is not None:
                self._tracker.stop()
            return

        self._unavailable = False
        self._last_px = None
        self._ext_pos = None
        if _IS_WAYLAND:
            if self._tracker is None:
                from kwincursor import KWinCursorTracker
                self._tracker = KWinCursorTracker()
            self._tracker.start()   # fora do KDE falha em silêncio
        backend = region_backend() if _IS_WAYLAND else None
        self._use_live = _IS_WAYLAND and backend is None
        if self._use_live:
            # Último recurso: portal de ScreenCast (pede permissão)
            from livegrab import LiveFrameSource
            if self._live is None:
                self._live = LiveFrameSource()
            self._live.start(self._ls_screen or self._screen_at(QCursor.pos()))
            # O portal pode abrir um diálogo de permissão — espera generosa
            self._live_deadline = time.monotonic() + 15
            self._live_probe = 0
            self._timer.setInterval(33)   # recorte local é barato
        elif backend == "kwin":
            self._timer.setInterval(33)   # DBus silencioso ~8ms por frame
        else:
            interval = 66 if _IS_WAYLAND else 16
            self._timer.setInterval(interval)
        if self._ls_wanted and self._lsw_ptr is None:
            import layershell
            self._lsw_ptr = layershell.apply(
                self,
                layer=layershell.LAYER_OVERLAY,
                exclusive_zone=-1,
                initial_pos=(0, 0),
                screen=self._ls_screen,
            )
            if self._lsw_ptr is None:
                self._ls_wanted = False   # sem layer-shell: segue com move()

        self._timer.start()
        self.update()
        self.show()

    # ── Internal ──────────────────────────────────────────────────────────

    def _screen_at(self, pos: QPoint):
        for s in QApplication.screens():
            if s.geometry().contains(pos):
                return s
        return QApplication.primaryScreen()

    def _tick(self):
        pos = self._tracker.pos() if self._tracker is not None else None
        if pos is None:
            pos = self._ext_pos if self._ext_pos is not None else QCursor.pos()

        # Cursor cruzou para outro monitor: migra o surface layer-shell
        if self._lsw_ptr and self._ls_screen is not None:
            scr = self._screen_at(pos)
            if scr is not self._ls_screen:
                self.change_screen(scr)

        if pos == self._cursor_pos:
            # Ao vivo, o conteúdo muda mesmo com o cursor parado
            if not self._use_live:
                return
        else:
            self._cursor_pos = pos
            self._reposition(pos)

        cap_size = DIAMETER // self._zoom
        cx, cy = pos.x(), pos.y()
        x = cx - cap_size // 2
        y = cy - cap_size // 2

        # Não amplia a toolbar: se a região capturada a alcançaria,
        # a lupa se recolhe até o cursor se afastar
        avoid = self._avoid() if self._avoid is not None else None
        if avoid is not None and QRect(x, y, cap_size, cap_size).intersects(
                avoid.adjusted(-8, -8, 8, 8)):
            if self.isVisible():
                self.hide()
            return
        if not self.isVisible():
            self.show()

        if self._use_live:
            # A autorização do KWin pode chegar segundos após o arranque
            # (kbuildsycoca) — troca para o backend silencioso assim que der
            self._live_probe += 1
            if self._live_probe >= 60:      # ~2s a 33ms
                self._live_probe = 0
                if region_backend() == "kwin":
                    self._live.stop()
                    self._use_live = False

        if self._use_live:
            screen = self._screen_at(pos)
            if self._live.screen is not screen:
                self._live.start(screen)
                self._live_deadline = time.monotonic() + 15
            px = self._live.grab(x, y, cap_size, cap_size)
        else:
            px = grab_region(x, y, cap_size, cap_size)

        if px is None:
            # Ao vivo: enquanto o helper vive e o prazo não estourou, é só
            # o portal a preparar a sessão — não marca indisponível ainda
            if self._use_live and self._live.running \
                    and time.monotonic() < self._live_deadline:
                return
            if not self._unavailable:
                self._unavailable = True
                self.update()
        else:
            self._unavailable = False
            self._last_px = px.scaled(
                DIAMETER, DIAMETER,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.update()

    def _gap(self) -> int:
        """Distância cursor→lupa: metade da região capturada + folga.

        Com folga menor, a borda da própria lupa entra na captura e
        aparece como um arco dentro do zoom."""
        return DIAMETER // (2 * self._zoom) + 12

    def _reposition(self, cursor: QPoint):
        # Layer-shell: o surface vive num monitor fixo — clampa nele
        ls = self._ls_screen if self._lsw_ptr else None
        screen = (ls or self._screen_at(cursor)).geometry()
        gap = self._gap()
        half = DIAMETER // 2

        # Candidatos em ordem de preferência: abaixo, acima, direita,
        # esquerda do cursor — o primeiro que cabe na tela sem tocar a
        # toolbar vence
        candidates = (
            QRect(cursor.x() - half, cursor.y() + gap, DIAMETER, DIAMETER),
            QRect(cursor.x() - half, cursor.y() - gap - DIAMETER,
                  DIAMETER, DIAMETER),
            QRect(cursor.x() + gap, cursor.y() - half, DIAMETER, DIAMETER),
            QRect(cursor.x() - gap - DIAMETER, cursor.y() - half,
                  DIAMETER, DIAMETER),
        )
        avoid = self._avoid() if self._avoid is not None else None
        rect = None
        for cand in candidates:
            if not screen.contains(cand):
                continue
            if avoid is not None and cand.intersects(avoid):
                continue
            rect = cand
            break
        if rect is None:
            # Nada coube inteiro (cantos): abaixo/acima do cursor + clamp
            rect = candidates[0]
            if rect.bottom() > screen.bottom():
                rect = candidates[1]

        x = max(screen.left(),  min(rect.x(), screen.right()  - DIAMETER))
        y = max(screen.top(),   min(rect.y(), screen.bottom() - DIAMETER))

        if self._lsw_ptr:
            # Margens layer-shell são relativas ao output, não absolutas
            import layershell
            layershell.move_to(self._lsw_ptr,
                               x - screen.left(), y - screen.top())
        else:
            self.move(x, y)

    # ── Painting ──────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        clip = QPainterPath()
        clip.addEllipse(0, 0, DIAMETER, DIAMETER)
        painter.setClipPath(clip)

        if self._unavailable or self._last_px is None:
            self._draw_unavailable(painter)
        else:
            painter.drawPixmap(0, 0, self._last_px)

        painter.setClipping(False)

        # Borda branca
        painter.setPen(QPen(QColor(255, 255, 255, 220), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(2, 2, DIAMETER - 4, DIAMETER - 4)

        # Anel escuro externo
        painter.setPen(QPen(QColor(0, 0, 0, 100), 1))
        painter.drawEllipse(0, 0, DIAMETER, DIAMETER)

        # Mira central
        mid = DIAMETER // 2
        painter.setPen(QPen(QColor(255, 50, 50, 200), 1))
        painter.drawLine(mid - 12, mid, mid - 4, mid)
        painter.drawLine(mid + 4,  mid, mid + 12, mid)
        painter.drawLine(mid, mid - 12, mid, mid - 4)
        painter.drawLine(mid, mid + 4,  mid, mid + 12)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 50, 50, 200)))
        painter.drawEllipse(mid - 2, mid - 2, 4, 4)

        painter.end()

    def _draw_unavailable(self, painter: QPainter):
        """Mostra o estado quando ainda não há imagem para ampliar."""
        if self._unavailable:
            msg = ("Lupa indisponível\nPermita a captura\nde tela e reative"
                   if self._use_live else
                   "Lupa indisponível\nInstale o 'grim'\npara Wayland")
        else:
            msg = "Iniciando captura…"
        painter.fillRect(0, 0, DIAMETER, DIAMETER, QColor(30, 30, 40, 220))
        painter.setPen(QColor(255, 255, 255, 200))
        font = QFont("Sans Serif", 9)
        painter.setFont(font)
        painter.drawText(
            QRect(10, DIAMETER // 2 - 30, DIAMETER - 20, 60),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            msg,
        )

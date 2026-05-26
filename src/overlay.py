import os
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QScreen, QPainterPath,
    QRadialGradient, QBrush, QPixmap, QFont,
)
from cursors import make_pen_cursor, make_eraser_cursor, make_crosshair_cursor

LASER_TRAIL_LEN = 18

# True quando rodando no backend Wayland nativo (não XWayland)
IS_WAYLAND = (
    os.environ.get("WAYLAND_DISPLAY") is not None
    and os.environ.get("QT_QPA_PLATFORM", "wayland") != "xcb"
)


class OverlayWindow(QWidget):
    """Janela transparente que cobre toda a tela para desenho."""

    # Emitido quando ferramenta texto activa e o utilizador clica na tela.
    # A toolbar conecta-se para abrir o diálogo de configuração de texto.
    text_placement_requested = pyqtSignal(QPoint)

    def __init__(self):
        super().__init__()
        self._strokes: list[list[tuple[QPoint, dict]]] = []
        self._current_stroke: list[tuple[QPoint, dict]] = []
        self._undo_stack: list[list[tuple[QPoint, dict]]] = []

        self._tool = "pen"
        self._color = QColor("#FF0000")
        self._size = 3
        self._drawing = False
        self._active = True

        # drag tool state
        self._drag_stroke_idx: int | None = None
        self._drag_last_pos: QPoint | None = None
        self._drag_hover_idx: int | None = None
        self._drag_linked_erasers: list[int] = []  # erasers ancorados ao stroke activo

        # laser
        self._laser_pos: QPoint | None = None
        self._laser_trail: list[QPoint] = []

        # modos de fundo
        self._whiteboard = False
        self._spotlight = False
        self._spotlight_pos: QPoint | None = None
        self._spotlight_radius = 150

        # whiteboard infinito
        self._wb_pan: QPointF = QPointF(0.0, 0.0)
        self._wb_zoom: float = 1.0
        self._wb_bg: QColor = QColor(255, 255, 255, 255)
        self._wb_panning: bool = False
        self._wb_pan_start_mouse: QPoint | None = None
        self._wb_pan_start_val: QPointF | None = None

        # Canvas acumulado: strokes concluídos são commitados aqui uma vez.
        # paintEvent faz blit deste pixmap + desenha apenas o stroke actual.
        # Invalida (None) quando o widget é redimensionado ou undo/clear ocorre.
        self._canvas: QPixmap | None = None

        # Scratch canvas para a borracha activa: cópia do canvas principal onde
        # cada novo segmento apagado é aplicado incrementalmente durante o drag.
        # paintEvent faz blit deste em vez de canvas+stroke enquanto está activo.
        self._erase_scratch: QPixmap | None = None

        # rect global da toolbar — usado no modo dois-janelas (legado)
        self._toolbar_global_rect = None
        # widget da toolbar embutida — usado no modo janela-única
        self._toolbar_widget = None
        # True quando layer-shell está ativo: show/hide usam máscara em vez de
        # mapear/desmapar a superfície (evita perder a configuração layer-shell)
        self._layer_shell_active = False
        # Chamado após remapar a superfície (layer-shell); usado em main.py
        # para forçar o toolbar de volta ao topo da z-order.
        self._on_remapped = None
        # True quando em modo pass-through: desenhos visíveis, input vai para apps abaixo.
        # Diferente de set_active(False) que oculta o overlay completamente.
        self._passthrough = False

        self._setup_window()
        self._refresh_cursor()

    def _setup_window(self):
        virtual_geo = QApplication.primaryScreen().virtualGeometry()
        for screen in QApplication.screens():
            virtual_geo = virtual_geo.united(screen.geometry())
        self.setGeometry(virtual_geo)

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus  # não rouba foco de outras apps
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        app = QApplication.instance()
        app.screenAdded.connect(self._on_screens_changed)
        app.screenRemoved.connect(self._on_screens_changed)

    def _on_screens_changed(self, _screen=None):
        virtual_geo = QApplication.primaryScreen().virtualGeometry()
        for s in QApplication.screens():
            virtual_geo = virtual_geo.united(s.geometry())
        self.setGeometry(virtual_geo)
        self._apply_input_mask()

    # ── Cursores ──────────────────────────────────────────────────────────

    def _refresh_cursor(self):
        """Atualiza o cursor de acordo com a ferramenta e estado atuais."""
        if not self._active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._wb_panning:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self._tool == "laser":
            self.setCursor(Qt.CursorShape.BlankCursor)
        elif self._tool == "eraser":
            self.setCursor(make_eraser_cursor(self._size))
        elif self._tool in ("line", "rect", "circle"):
            self.setCursor(make_crosshair_cursor())
        elif self._tool == "drag":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self._tool == "text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:  # pen, highlighter
            self.setCursor(make_pen_cursor(self._color))

    # ── Public API ────────────────────────────────────────────────────────

    def set_tool(self, tool: str):
        self._tool = tool
        self._laser_pos = None
        self._laser_trail.clear()
        self._drag_hover_idx = None
        self._drag_stroke_idx = None
        self._update_tracking()
        self._refresh_cursor()
        self.update()

    def set_color(self, color: QColor):
        self._color = color
        if self._tool in ("pen", "highlighter"):
            self.setCursor(make_pen_cursor(color))

    def set_size(self, size: int):
        self._size = size
        if self._tool == "eraser":
            self.setCursor(make_eraser_cursor(size))

    def place_text(self, pos: QPoint, text: str,
                   font_family: str, font_size: int, color: QColor):
        """Insere um item de texto na posição indicada (coords de ecrã)."""
        if not text.strip():
            return
        stroke = [(pos, {
            "tool": "text",
            "color": QColor(color),
            "size": font_size,
            "font_family": font_family,
            "text": text,
        })]
        self._strokes.append(stroke)
        self._undo_stack.clear()
        self._ensure_canvas()
        self._commit_stroke(stroke)
        self.update()

    def embed_toolbar(self, toolbar):
        """
        Embeds toolbar as a child widget of this overlay (single-window mode).
        Call BEFORE show(). The toolbar's saved position is preserved via its _cfg.
        """
        pos = toolbar._cfg.get("toolbar_pos", {"x": 20, "y": 150})
        toolbar.setParent(self)
        # WA_TranslucentBackground num filho de janela transparente torna o filho invisível;
        # sem ele, as áreas não pintadas caem no fundo transparente do pai (correto).
        toolbar.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        # setParent() resets widget position to (0,0) — restore from config
        toolbar.move(pos.get("x", 20), pos.get("y", 150))
        toolbar.show()
        self._toolbar_widget = toolbar
        self._apply_input_mask()

    def set_toolbar_region(self, local_rect):
        """Legado: chamado pelo toolbar para sincronizar máscara (modo dois-janelas)."""
        self._toolbar_global_rect = local_rect
        self._apply_input_mask()

    def _apply_input_mask(self):
        from PyQt6.QtGui import QRegion

        if self._toolbar_widget is not None:
            # Embed (GNOME): usa QWindow.setMask para restringir input à toolbar.
            # QWindow.setMask envia wl_surface_set_input_region sem clipar o rendering —
            # os desenhos ficam visíveis enquanto o diálogo está aberto.
            wh = self.windowHandle()
            if self._active:
                # Restaura set_input_region(NULL) = aceita tudo
                if wh:
                    wh.setMask(QRegion())
                self.clearMask()
            else:
                tb_rect = self._toolbar_widget.geometry()
                region = (QRegion(tb_rect) if not tb_rect.isEmpty()
                          else self._offscreen_region())
                if wh:
                    wh.setMask(region)  # input restrito à toolbar; rendering inalterado
                # Não chamar self.setMask() — cliparia também o rendering
            return

        if self._layer_shell_active:
            # Layer-shell duas janelas: o toolbar é superfície separada acima na z-order.
            # O compositor entrega cliques ao toolbar sem precisar de máscara no overlay.
            # widget.geometry() é lixo no Wayland — qualquer subtração seria errada.
            if self._active:
                self.clearMask()
            # inactive: set_active() já chamou super().hide() — nada a fazer aqui
            return

        # Modo legado dois-janelas (X11 / sem layer-shell)
        if not self._active:
            self.setMask(self._offscreen_region())
            return

        # Área base: tela toda
        full = QRegion(self.rect())
        origin = self.geometry().topLeft()

        # Subtrai área da toolbar (modo legado dois-janelas)
        if self._toolbar_global_rect is not None:
            local = self._toolbar_global_rect.translated(-origin)
            full = full.subtracted(QRegion(local))

        # Subtrai área de painel de cada tela (availableGeometry ≠ geometry)
        # para que o painel KDE sempre receba eventos de input.
        for screen in QApplication.screens():
            panel = QRegion(screen.geometry().translated(-origin)).subtracted(
                        QRegion(screen.availableGeometry().translated(-origin)))
            if not panel.isEmpty():
                full = full.subtracted(panel)

        self.setMask(full)

    def set_active(self, active: bool):
        """Ativa ou desativa o overlay. False oculta completamente (sem desenhos)."""
        # Ao esconder, cancela qualquer passthrough activo
        if not active and self._passthrough:
            self._passthrough = False
            self.clearMask()
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        self._active = active
        if self._toolbar_widget is not None:
            self._apply_input_mask()
            self._refresh_cursor()
            self.update()
        elif self._layer_shell_active:
            # Layer-shell: activo → mostra superfície; inactivo → oculta (desmapeia)
            if active:
                was_hidden = not self.isVisible()
                if was_hidden:
                    super().show()
                self.clearMask()
                self._refresh_cursor()
                self.update()
                if was_hidden and self._on_remapped:
                    self._on_remapped()
            else:
                super().hide()
        else:
            # X11 / fallback
            if active:
                super().show()
                self.raise_()
                self.clearMask()
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
                self._apply_input_mask()
                self._refresh_cursor()
            else:
                super().hide()

    def set_passthrough(self, active: bool):
        """Modo seta: desenhos visíveis mas input passa para apps abaixo.

        Usa setMask(offscreen_region) para enviar wl_surface_set_input_region com
        um rectângulo fora do ecrã. O compositor não entrega nenhum evento de input
        à superfície porque o rectângulo não intersecta a área visível.
        Nota: QRegion() vazio é mapeado pelo Qt para set_input_region(NULL) = aceita
        tudo — por isso usa-se um rectângulo offscreen em vez de região vazia.
        """
        self._passthrough = active
        self._refresh_cursor()
        self.update()
        if active:
            if not self.isVisible():
                super().show()
                if not self._layer_shell_active:
                    self.raise_()
            offscreen = self._offscreen_region()
            wh = self.windowHandle()
            if wh:
                # QWindow.setMask → wl_surface_set_input_region(offscreen_rect) + commit
                # NÃO chamar self.setMask(offscreen): QWidget.setMask clipa também o
                # rendering (drawings tornam-se invisíveis).
                wh.setMask(offscreen)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        else:
            from PyQt6.QtGui import QRegion
            wh = self.windowHandle()
            if wh:
                # clearMask() faz early-return se widget mask já era vazia (não chama
                # QWindow.setMask) — chamar explicitamente para enviar set_input_region(NULL).
                wh.setMask(QRegion())
            self.clearMask()
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            if not self._layer_shell_active:
                self._apply_input_mask()

    def show(self):
        if self._layer_shell_active:
            self._active = True
            self.clearMask()
            self._refresh_cursor()
            self.update()
            if not self.isVisible():
                super().show()  # primeira vez: cria superfície layer-shell
        else:
            super().show()

    def hide(self):
        self._active = False
        super().hide()

    def _offscreen_region(self):
        """1×1 px fora do ecrã: wl_surface recebe set_input_region(empty_region)
        em vez de set_input_region(null) que o Qt usa para QRegion() vazio."""
        from PyQt6.QtGui import QRegion
        return QRegion(max(self.width(), 9999) + 1, 0, 1, 1)

    def _remap_layer(self, layer: int) -> None:
        """Remapeia a wl_layer_surface na camada indicada sem alterar _active.

        LayerShellQt aplica a camada na recriação da superfície (hide+show).
        Não chama _on_remapped — esse callback é reservado para toggle de modo
        de desenho. Usar QWidget.hide/show para contornar os overrides que
        alterariam _active e disparariam lógica de passthrough.
        """
        if not self._layer_shell_active or not self._lsw_ptr:
            return
        import layershell as _ls
        _ls.set_layer(self._lsw_ptr, layer)
        QWidget.hide(self)
        QWidget.show(self)
        self.clearMask()
        self._refresh_cursor()
        self.update()

    def change_screen(self, screen):
        """Move o overlay para outro monitor (layer-shell).

        hide+show força a recriação da wl_layer_surface no novo output.
        Não dispara _on_remapped (que é reservado para toggle de modo de desenho).
        """
        if not self._layer_shell_active:
            return
        qwindow = self.windowHandle()
        if qwindow is None:
            return
        was_visible = self.isVisible()
        if was_visible:
            super().hide()
        qwindow.setScreen(screen)
        if was_visible:
            super().show()
            if self._active:
                self.clearMask()
                self._refresh_cursor()
                self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._layer_shell_active:
            self._apply_input_mask()

    def set_whiteboard(self, active: bool):
        self._whiteboard = active
        if not active:
            self._wb_pan = QPointF(0.0, 0.0)
            self._wb_zoom = 1.0
            self._wb_panning = False
            self._canvas = None  # força rebuild com os strokes desenhados no whiteboard
        self._update_tracking()
        self.update()

    def set_whiteboard_bg(self, color: QColor):
        self._wb_bg = QColor(color)
        self.update()

    def set_spotlight(self, active: bool):
        self._spotlight = active
        self._update_tracking()
        if not active:
            self._spotlight_pos = None
        self.update()

    def set_spotlight_radius(self, radius: int):
        self._spotlight_radius = radius
        self.update()

    # ── Canvas acumulado ──────────────────────────────────────────────────

    def _ensure_canvas(self):
        if self._canvas is None or self._canvas.size() != self.size():
            self._canvas = QPixmap(self.size())
            self._canvas.fill(Qt.GlobalColor.transparent)
            self._rebuild_canvas()

    def _rebuild_canvas(self):
        """Redesenha todos os strokes concluídos no canvas (usado por undo/resize)."""
        if self._canvas is None:
            return
        self._canvas.fill(Qt.GlobalColor.transparent)
        if not self._strokes:
            return
        p = QPainter(self._canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for stroke in self._strokes:
            self._draw_stroke(p, stroke)
        p.end()

    def _commit_stroke(self, stroke: list) -> None:
        """Pinta um stroke directamente no canvas sem redesenhar tudo."""
        if self._canvas is None:
            return
        p = QPainter(self._canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_stroke(p, stroke)
        p.end()

    # ── Undo / redo / clear ───────────────────────────────────────────────

    def undo(self):
        if self._strokes:
            self._undo_stack.append(self._strokes.pop())
            self._erase_scratch = None
            self._canvas = None   # força rebuild no próximo paint
            self.update()

    def redo(self):
        if self._undo_stack:
            stroke = self._undo_stack.pop()
            self._strokes.append(stroke)
            if not self._whiteboard:
                self._ensure_canvas()
                self._commit_stroke(stroke)
            self.update()

    def clear(self):
        self._strokes.clear()
        self._undo_stack.clear()
        self._erase_scratch = None
        if self._canvas is not None:
            self._canvas.fill(Qt.GlobalColor.transparent)
        self.update()

    # ── Drag tool helpers ─────────────────────────────────────────────────────

    _DRAG_HANDLE_R = 12.0  # raio do círculo visual E da área de clique

    @staticmethod
    def _stroke_anchor(stroke) -> QPointF:
        """Retorna o ponto âncora do stroke (posição p/ texto, centroide p/ demais)."""
        if stroke[0][1].get("tool") == "text":
            p = stroke[0][0]
            return QPointF(p.x(), p.y())
        pts = [pt for pt, _ in stroke]
        return QPointF(
            sum(p.x() for p in pts) / len(pts),
            sum(p.y() for p in pts) / len(pts),
        )

    def _find_linked_erasers(self, stroke_idx: int) -> list[int]:
        """Índices de strokes de borracha posteriores que cobrem stroke_idx.

        Um stroke de borracha é considerado 'ancorado' ao stroke_idx se qualquer
        ponto do traço de borracha está dentro do raio da borracha de qualquer
        ponto do stroke_idx. Estes erasers movem-se junto quando stroke_idx é arrastado.
        """
        stroke = self._strokes[stroke_idx]
        if not stroke:
            return []
        pen_pts = [(pt.x(), pt.y()) for pt, _ in stroke]
        if not pen_pts:
            return []
        min_x = min(p[0] for p in pen_pts)
        max_x = max(p[0] for p in pen_pts)
        min_y = min(p[1] for p in pen_pts)
        max_y = max(p[1] for p in pen_pts)

        linked = []
        for j in range(stroke_idx + 1, len(self._strokes)):
            s = self._strokes[j]
            if not s or s[0][1].get("tool") != "eraser":
                continue
            eraser_r = s[0][1].get("size", 3) * 2
            r2 = eraser_r * eraser_r
            overlaps = False
            for pt, _ in s:
                ex, ey = pt.x(), pt.y()
                if (ex < min_x - eraser_r or ex > max_x + eraser_r
                        or ey < min_y - eraser_r or ey > max_y + eraser_r):
                    continue
                for px, py in pen_pts:
                    if (ex - px) ** 2 + (ey - py) ** 2 <= r2:
                        overlaps = True
                        break
                if overlaps:
                    break
            if overlaps:
                linked.append(j)
        return linked

    def _is_fully_erased(self, idx: int) -> bool:
        """True se todos os pontos amostrados do stroke estão cobertos por erasers posteriores."""
        stroke = self._strokes[idx]
        if not stroke:
            return True
        later_erasers: list[tuple[float, list[tuple[float, float]]]] = []
        for j in range(idx + 1, len(self._strokes)):
            s = self._strokes[j]
            if not s or s[0][1].get("tool") != "eraser":
                continue
            r = s[0][1].get("size", 3) * 2
            later_erasers.append((r * r, [(pt.x(), pt.y()) for pt, _ in s]))
        if not later_erasers:
            return False
        all_pts = [pt for pt, _ in stroke]
        step = max(1, len(all_pts) // 10)
        for spt in all_pts[::step]:
            sx, sy = spt.x(), spt.y()
            covered = False
            for r2, epts in later_erasers:
                for ex, ey in epts:
                    if (ex - sx) ** 2 + (ey - sy) ** 2 <= r2:
                        covered = True
                        break
                if covered:
                    break
            if not covered:
                return False
        return True

    def _find_stroke_at(self, pos: QPoint) -> "int | None":
        """Retorna o índice do stroke cujo círculo de arrasto contém pos."""
        # Converte pos de ecrã para canvas; o raio também fica em unidades de canvas
        cp = self._to_canvas(pos)
        px, py = cp.x(), cp.y()
        z = self._wb_zoom if (self._whiteboard and self._wb_zoom != 0) else 1.0
        r2 = (self._DRAG_HANDLE_R / z) ** 2
        best_idx, best_dist2 = None, float("inf")
        for i, stroke in enumerate(self._strokes):
            if not stroke:
                continue
            if stroke[0][1].get("tool") == "eraser":
                continue  # erasers movem-se com o stroke a que pertencem, não são arrastáveis por si
            if self._is_fully_erased(i):
                continue
            a = self._stroke_anchor(stroke)
            d2 = (a.x() - px) ** 2 + (a.y() - py) ** 2
            if d2 < best_dist2:
                best_dist2, best_idx = d2, i
        return best_idx if best_idx is not None and best_dist2 <= r2 else None

    def _translate_stroke(self, idx: int, delta: QPoint):
        self._strokes[idx] = [
            (QPointF(pt.x() + delta.x(), pt.y() + delta.y()), props)
            for pt, props in self._strokes[idx]
        ]

    # Tamanho mínimo (px) do bounding box para escalar para baixo
    _SCALE_MIN_SIZE = 8.0

    def _scale_stroke(self, idx: int, factor: float):
        stroke = self._strokes[idx]
        if not stroke:
            return
        if stroke[0][1].get("tool") == "text":
            pt, props = stroke[0]
            new_size = props["size"] * factor
            if new_size < 6:
                return  # tamanho mínimo
            self._strokes[idx] = [(pt, {**props, "size": new_size})]
        else:
            pts = [pt for pt, _ in stroke]
            cx = sum(p.x() for p in pts) / len(pts)
            cy = sum(p.y() for p in pts) / len(pts)
            new_pts = [
                QPointF(cx + (pt.x() - cx) * factor,
                        cy + (pt.y() - cy) * factor)
                for pt in pts
            ]
            # Recusa escalar para baixo se ficaria menor que o mínimo
            if factor < 1.0:
                xs = [p.x() for p in new_pts]
                ys = [p.y() for p in new_pts]
                if (max(xs) - min(xs)) < self._SCALE_MIN_SIZE and \
                   (max(ys) - min(ys)) < self._SCALE_MIN_SIZE:
                    return
            self._strokes[idx] = [
                (QPointF(cx + (pt.x() - cx) * factor,
                         cy + (pt.y() - cy) * factor), props)
                for pt, props in stroke
            ]
        self._canvas = None
        self.update()

    # ── Mouse events ──────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        # Botão do meio OU Shift+esquerdo: inicia pan no modo whiteboard
        _shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        _is_pan_trigger = (
            event.button() == Qt.MouseButton.MiddleButton
            or (event.button() == Qt.MouseButton.LeftButton and _shift)
        )
        if _is_pan_trigger and self._whiteboard:
            self._wb_panning = True
            self._wb_pan_start_mouse = event.pos()
            self._wb_pan_start_val = QPointF(self._wb_pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if not self._active or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._tool == "laser":
            return
        if self._tool == "drag":
            idx = self._find_stroke_at(event.pos())
            if idx is not None:
                self._drag_stroke_idx = idx
                self._drag_linked_erasers = self._find_linked_erasers(idx)
                self._drag_last_pos = event.pos()
                self._drawing = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self._tool == "text":
            self.text_placement_requested.emit(event.pos())
            return
        self._drawing = True
        self._current_stroke = [(self._to_canvas(event.pos()), self._brush_props())]
        self._undo_stack.clear()
        # Borracha: scratch canvas (apenas fora do whiteboard — no wb renderiza ao vivo)
        if self._tool == "eraser" and not self._whiteboard:
            self._ensure_canvas()
            self._erase_scratch = QPixmap(self._canvas)
            p = QPainter(self._erase_scratch)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._draw_stroke(p, self._current_stroke)
            p.end()

    def mouseMoveEvent(self, event):
        pos = event.pos()

        # Pan whiteboard com botão do meio
        if self._wb_panning and self._wb_pan_start_mouse is not None:
            delta = pos - self._wb_pan_start_mouse
            self._wb_pan = QPointF(
                self._wb_pan_start_val.x() + delta.x(),
                self._wb_pan_start_val.y() + delta.y(),
            )
            self.update()
            return

        # Cursor mão aberta no whiteboard quando Shift segurado (indica que pode arrastar)
        if self._whiteboard and not self._wb_panning and not self._drawing:
            _shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if _shift:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self._refresh_cursor()

        if self._spotlight:
            self._spotlight_pos = pos
            self.update()

        if self._tool == "drag":
            if self._drawing and self._drag_stroke_idx is not None:
                dx = pos.x() - self._drag_last_pos.x()
                dy = pos.y() - self._drag_last_pos.y()
                if self._whiteboard and self._wb_zoom != 0:
                    dx /= self._wb_zoom
                    dy /= self._wb_zoom
                delta = QPointF(dx, dy)
                self._translate_stroke(self._drag_stroke_idx, delta)
                for j in self._drag_linked_erasers:
                    if j < len(self._strokes):
                        self._translate_stroke(j, delta)
                self._drag_last_pos = pos
                self._canvas = None
                self.update()
            else:
                new_hover = self._find_stroke_at(pos)
                if new_hover != self._drag_hover_idx:
                    self._drag_hover_idx = new_hover
                    self.update()
            return

        if self._tool == "laser":
            self._laser_trail.append(pos)
            if len(self._laser_trail) > LASER_TRAIL_LEN:
                self._laser_trail.pop(0)
            self._laser_pos = pos
            self.update()
            return

        if not self._drawing:
            return
        self._current_stroke.append((self._to_canvas(pos), self._brush_props()))

        # Borracha: aplica apenas o novo segmento no scratch — O(1) por frame (só fora do wb)
        if self._tool == "eraser" and self._erase_scratch is not None:
            pts = self._current_stroke
            if len(pts) >= 2:
                p = QPainter(self._erase_scratch)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_stroke(p, pts[-2:])
                p.end()

        self.update()

    def mouseReleaseEvent(self, event):
        # Fim do pan (botão do meio ou esquerdo com Shift)
        if self._wb_panning and event.button() in (
            Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton
        ):
            self._wb_panning = False
            self._wb_pan_start_mouse = None
            self._wb_pan_start_val = None
            self._refresh_cursor()
            event.accept()
            return

        if self._tool == "drag":
            if self._drawing:
                self._drawing = False
                self._drag_stroke_idx = None
                self._drag_linked_erasers = []
                self._drag_last_pos = None
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self._tool == "laser" or not self._drawing:
            return
        self._drawing = False
        if self._current_stroke:
            stroke = list(self._current_stroke)
            self._strokes.append(stroke)
            if self._whiteboard:
                pass  # whiteboard renderiza direto de _strokes, sem cache
            elif self._erase_scratch is not None:
                # Borracha: scratch já tem o resultado final — promove a canvas
                self._canvas = self._erase_scratch
                self._erase_scratch = None
            else:
                self._ensure_canvas()
                self._commit_stroke(stroke)
        self._current_stroke = []
        self.update()

    def wheelEvent(self, event):
        if self._whiteboard and not (self._tool == "drag"):
            _shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if _shift:
                # Zoom centrado no cursor
                delta_y = event.angleDelta().y()
                if delta_y != 0:
                    factor = 1.1 if delta_y > 0 else 0.9
                    cp = event.position()
                    self._wb_pan = QPointF(
                        cp.x() * (1.0 - factor) + self._wb_pan.x() * factor,
                        cp.y() * (1.0 - factor) + self._wb_pan.y() * factor,
                    )
                    self._wb_zoom *= factor
                    self._canvas = None
            else:
                delta = event.angleDelta()
                self._wb_pan += QPointF(delta.x() / 8.0, delta.y() / 8.0)
            self.update()
            event.accept()
            return
        if self._active and self._tool == "drag":
            if self._drawing and self._drag_stroke_idx is not None:
                idx = self._drag_stroke_idx
            else:
                idx = self._find_stroke_at(event.position().toPoint())
            if idx is not None:
                factor = 1.1 if event.angleDelta().y() > 0 else 0.9
                self._scale_stroke(idx, factor)
            event.accept()
        else:
            event.ignore()

    # ── Painting ──────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._erase_scratch = None
        self._canvas = None   # tamanho mudou — recria no próximo paint

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._whiteboard:
            # Renderiza em pixmap intermediário para compositing correto da borracha
            wb_px = QPixmap(self.size())
            wb_px.fill(self._wb_bg)
            wb_p = QPainter(wb_px)
            wb_p.setRenderHint(QPainter.RenderHint.Antialiasing)
            wb_p.translate(self._wb_pan)
            wb_p.scale(self._wb_zoom, self._wb_zoom)
            for stroke in self._strokes:
                self._draw_stroke(wb_p, stroke)
            if self._current_stroke:
                self._draw_stroke(wb_p, self._current_stroke)
            if self._tool == "drag" and self._strokes:
                self._draw_drag_handles(wb_p)
            wb_p.end()
            painter.drawPixmap(0, 0, wb_px)
        else:
            self._ensure_canvas()
            # Borracha activa: blit do scratch (já tem o apagado acumulado) — O(1)
            if self._erase_scratch is not None:
                painter.drawPixmap(0, 0, self._erase_scratch)
            else:
                painter.drawPixmap(0, 0, self._canvas)
                if self._current_stroke:
                    self._draw_stroke(painter, self._current_stroke)
            if self._tool == "drag" and self._strokes:
                self._draw_drag_handles(painter)

        if self._tool == "laser" and self._laser_pos:
            self._draw_laser(painter)

        if self._spotlight:
            self._draw_spotlight(painter)

        painter.end()

    def _draw_stroke(self, painter: QPainter, stroke: list):
        if not stroke:
            return

        props = stroke[0][1]
        tool, color, size = props["tool"], props["color"], props["size"]

        if tool == "eraser":
            if self._whiteboard:
                # No whiteboard, "apagar" = pintar com cor de fundo
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceOver)
                pen = QPen(self._wb_bg, size * 4, Qt.PenStyle.SolidLine,
                           Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            else:
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Clear)
                pen = QPen(Qt.GlobalColor.transparent, size * 4, Qt.PenStyle.SolidLine,
                           Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        elif tool == "highlighter":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            hi = QColor(color)
            hi.setAlpha(80)
            pen = QPen(hi, size * 6, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        else:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(color, size, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # QPainterPath exige QPointF — converte aqui
        raw   = [p for p, _ in stroke]
        pts_f = [QPointF(p) for p in raw]

        if tool == "text":
            font = QFont(props.get("font_family", "Sans Serif"), props.get("size", 16))
            painter.setFont(font)
            painter.setPen(QPen(color))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawText(raw[0], props.get("text", ""))
        elif tool in ("pen", "highlighter", "eraser"):
            if len(pts_f) == 1:
                painter.drawPoint(pts_f[0])
            else:
                path = QPainterPath()
                path.moveTo(pts_f[0])
                for pt in pts_f[1:]:
                    path.lineTo(pt)
                painter.drawPath(path)
        elif tool == "line" and len(raw) >= 2:
            painter.drawLine(pts_f[0], pts_f[-1])
        elif tool == "rect" and len(raw) >= 2:
            painter.drawRect(QRectF(pts_f[0], pts_f[-1]).normalized())
        elif tool == "circle" and len(raw) >= 2:
            painter.drawEllipse(QRectF(pts_f[0], pts_f[-1]).normalized())

    def _draw_drag_handles(self, painter: QPainter):
        """Desenha ícones de arrasto (4 setas) em cada stroke quando drag tool ativo."""
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        for i, stroke in enumerate(self._strokes):
            if not stroke:
                continue
            if stroke[0][1].get("tool") == "eraser":
                continue  # sem handle para borracha — move junto com o stroke ancorado
            if self._is_fully_erased(i):
                continue

            anchor = self._stroke_anchor(stroke)

            is_active = (self._drawing and i == self._drag_stroke_idx)
            is_hover  = (not self._drawing and i == self._drag_hover_idx)

            if is_active:
                c = QColor(255, 200, 50, 230)   # amarelo: segurando
            elif is_hover:
                c = QColor(80, 200, 255, 230)   # azul: hover
            else:
                c = QColor(255, 255, 255, 150)  # branco: normal

            cx, cy = anchor.x(), anchor.y()
            R  = self._DRAG_HANDLE_R   # raio do círculo = área de clique
            AL = R * 0.65              # comprimento do braço da seta
            AW = R * 0.28              # semi-largura da cabeça da seta

            # Círculo de fundo (sombra)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 90)))
            painter.drawEllipse(QPointF(cx, cy), R, R)

            # Borda do círculo
            painter.setPen(QPen(c, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), R, R)

            # Setas: 4 triângulos preenchidos + linhas dos braços
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(c))
            for tip, w1, w2 in [
                (QPointF(cx,      cy - AL), QPointF(cx - AW, cy - AL + AW), QPointF(cx + AW, cy - AL + AW)),  # cima
                (QPointF(cx,      cy + AL), QPointF(cx - AW, cy + AL - AW), QPointF(cx + AW, cy + AL - AW)),  # baixo
                (QPointF(cx - AL, cy),      QPointF(cx - AL + AW, cy - AW), QPointF(cx - AL + AW, cy + AW)),  # esquerda
                (QPointF(cx + AL, cy),      QPointF(cx + AL - AW, cy - AW), QPointF(cx + AL - AW, cy + AW)),  # direita
            ]:
                tri = QPainterPath()
                tri.moveTo(tip); tri.lineTo(w1); tri.lineTo(w2)
                tri.closeSubpath()
                painter.drawPath(tri)

            # Linhas de cruz centrais
            painter.setPen(QPen(c, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            half = AL - AW
            painter.drawLine(QPointF(cx, cy - half), QPointF(cx, cy + half))
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))

    def _draw_spotlight(self, painter: QPainter):
        pos = self._spotlight_pos
        r = float(self._spotlight_radius)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(self.rect()))
        if pos:
            path.addEllipse(QPointF(pos.x(), pos.y()), r, r)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.fillPath(path, QColor(0, 0, 0, 170))

        if pos:
            gradient = QRadialGradient(QPointF(pos.x(), pos.y()), r + 30)
            gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
            gradient.setColorAt(0.7, QColor(0, 0, 0, 0))
            gradient.setColorAt(1.0, QColor(0, 0, 0, 100))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawEllipse(QPointF(pos.x(), pos.y()), r + 30, r + 30)

    def _draw_laser(self, painter: QPainter):
        trail = self._laser_trail
        count = len(trail)
        for i, pt in enumerate(trail[:-1]):
            t = i / max(count - 1, 1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(255, 50, 50, int(t * 160))))
            painter.drawEllipse(QPointF(pt), max(1.0, t * 6), max(1.0, t * 6))

        pos = self._laser_pos
        glow_r = 28.0
        gradient = QRadialGradient(pos.x(), pos.y(), glow_r)
        gradient.setColorAt(0.0,  QColor(255, 255, 255, 220))
        gradient.setColorAt(0.15, QColor(255, 60, 60, 200))
        gradient.setColorAt(0.45, QColor(220, 0, 0, 100))
        gradient.setColorAt(1.0,  QColor(180, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(pos), glow_r, glow_r)
        painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
        painter.drawEllipse(QPointF(pos), 4.0, 4.0)

    def _to_canvas(self, pos: QPoint) -> QPointF:
        """Converte posição de ecrã para coordenadas de canvas (desconta pan + zoom)."""
        if self._whiteboard:
            z = self._wb_zoom if self._wb_zoom != 0 else 1.0
            return QPointF(
                (pos.x() - self._wb_pan.x()) / z,
                (pos.y() - self._wb_pan.y()) / z,
            )
        return QPointF(pos)

    def _brush_props(self) -> dict:
        return {"tool": self._tool, "color": QColor(self._color), "size": self._size}

    def _update_tracking(self):
        self.setMouseTracking(
            (self._tool == "laser") or self._spotlight
            or self._whiteboard or (self._tool == "drag")
        )

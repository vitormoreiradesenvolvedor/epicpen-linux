import os
import math
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QScreen, QPainterPath,
    QRadialGradient, QBrush, QPixmap, QFont, QFontMetrics,
)
from cursors import (
    make_pen_cursor, make_eraser_cursor, make_crosshair_cursor,
    make_arrow_cursor,
)

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
    # Emitido quando duplo-clique num texto existente em drag mode (int = índice do stroke).
    text_edit_requested = pyqtSignal(int)
    # Posição do cursor (coords locais do overlay) a cada movimento. A lupa
    # usa isto no Wayland: QCursor.pos() global não é confiável com layer-shell.
    cursor_moved = pyqtSignal(QPoint)

    def __init__(self):
        super().__init__()
        self._strokes: list[list[tuple[QPoint, dict]]] = []
        self._current_stroke: list[tuple[QPoint, dict]] = []
        # Buffer de redo: cada entrada é ("add", stroke) ou ("erase", after_snap).
        # Limpo a cada nova acção; preenchido por undo().
        self._undo_stack: list = []
        # Histórico de acções desfeitas: lista de ("add",) ou ("erase", before_snap).
        # Não é limpo por novas acções — só cresce (release) e encolhe (undo).
        self._undo_ops: list = []

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
        self._magnifier_tracking = False   # lupa ativa: rastreia hover
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

        # [OBSOLETO — mantido apenas para compatibilidade; nunca mais criado]
        self._erase_scratch: QPixmap | None = None

        # Snapshot de _strokes no início de um traço de borracha (não-WB).
        # Usado para undo: registado em _undo_ops ao soltar o botão.
        self._erase_base: list | None = None

        # Scratch incremental para pen/highlighter activos (fora do whiteboard).
        # Cópia de _canvas; cada novo segmento é pintado aqui em O(1).
        # Promovido a _canvas no release (evita _commit_stroke + redraw full path).
        self._pen_scratch: QPixmap | None = None

        # Cache do whiteboard renderizado (bg + strokes committed, espaço de ecrã).
        # Rebuild apenas quando strokes, pan, zoom ou tamanho mudam — O(1) em frames
        # normais (laser, spotlight, pan sem strokes novos).
        self._wb_canvas: QPixmap | None = None

        # Base pré-renderizada para drag activo: todos os strokes EXCEPTO o arrastado.
        # Criado uma vez no press; durante o move faz-se blit base + draw stroke → O(1).
        # Elimina o _rebuild_canvas / _wb_canvas rebuild por frame durante o drag.
        self._drag_base: QPixmap | None = None

        # Stroke arrastado pré-renderizado num pixmap flutuante (coords de canvas).
        # Durante o move só o offset acumulado muda — paintEvent faz dois blits e
        # os pontos do stroke são transladados UMA vez, no release. O custo por
        # frame é independente do número de pontos do stroke.
        self._drag_pixmap: QPixmap | None = None
        self._drag_pix_pos = QPointF(0.0, 0.0)   # origem do pixmap em canvas
        self._drag_offset = QPointF(0.0, 0.0)    # delta acumulado do drag

        # Cache de âncoras (centroides) por índice de stroke. Evita recalcular
        # O(pontos) por stroke a cada frame em _draw_drag_handles/_find_stroke_at.
        # Invalidado junto com _erased_cache.
        self._anchor_cache: dict[int, QPointF] = {}

        # Cache de _is_fully_erased: dict[stroke_idx → bool].
        # Invalidado (limpo) sempre que a estrutura de _strokes muda.
        # Evita O(n²) por frame em _draw_drag_handles e _find_stroke_at.
        self._erased_cache: dict[int, bool] = {}

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
            # Seta de alta visibilidade — a mira fina sumia em telas claras
            self.setCursor(make_arrow_cursor(self._color))
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
        self._erase_scratch = None
        self._erase_base = None
        self._pen_scratch = None
        self._drag_base = None
        self._update_tracking()
        self._refresh_cursor()
        self.update()

    def set_color(self, color: QColor):
        self._color = color
        if self._tool in ("pen", "highlighter"):
            self.setCursor(make_pen_cursor(color))
        elif self._tool in ("line", "rect", "circle"):
            self.setCursor(make_arrow_cursor(color))

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
        self._undo_ops.append(("add",))
        self._undo_stack.clear()
        self._invalidate_erased_cache()
        if self._whiteboard:
            # Commit incremental no _wb_canvas (mesmo padrão de mouseReleaseEvent)
            if self._wb_canvas is not None:
                wb_p = QPainter(self._wb_canvas)
                wb_p.setRenderHint(QPainter.RenderHint.Antialiasing)
                wb_p.translate(self._wb_pan)
                wb_p.scale(self._wb_zoom, self._wb_zoom)
                self._draw_stroke(wb_p, stroke)
                wb_p.end()
            # se _wb_canvas é None, paintEvent recria do zero incluindo o texto
        else:
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
        self._wb_canvas = None   # sempre invalida (activação ou desactivação)
        self._update_tracking()
        self.update()

    def set_whiteboard_bg(self, color: QColor):
        self._wb_bg = QColor(color)
        self._wb_canvas = None  # cor de fundo mudou — rebuild necessário
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
        if not self._undo_ops:
            return
        op = self._undo_ops.pop()
        self._erase_scratch = None
        self._pen_scratch = None
        self._canvas = None
        self._wb_canvas = None
        self._invalidate_erased_cache()
        if op[0] == "add":
            if self._strokes:
                undone = self._strokes.pop()
                self._undo_stack.append(("add", undone))
        elif op[0] == "erase":
            _, before_snap = op
            after_snap = list(self._strokes)   # estado atual (pós-erase) para redo
            self._undo_stack.append(("erase", after_snap))
            self._strokes = list(before_snap)  # restaura estado pré-erase
        self.update()

    def redo(self):
        if not self._undo_stack:
            return
        entry = self._undo_stack[-1]
        if not (isinstance(entry, tuple) and len(entry) >= 2):
            self._undo_stack.pop()
            return
        kind = entry[0]
        if kind == "add":
            self._undo_stack.pop()
            _, stroke = entry
            self._strokes.append(stroke)
            self._undo_ops.append(("add",))
            self._invalidate_erased_cache()
            if self._whiteboard:
                if self._wb_canvas is not None:
                    wb_p = QPainter(self._wb_canvas)
                    wb_p.setRenderHint(QPainter.RenderHint.Antialiasing)
                    wb_p.translate(self._wb_pan)
                    wb_p.scale(self._wb_zoom, self._wb_zoom)
                    self._draw_stroke(wb_p, stroke)
                    wb_p.end()
            else:
                self._ensure_canvas()
                self._commit_stroke(stroke)
            self.update()
        elif kind == "erase":
            self._undo_stack.pop()
            _, after_snap = entry
            before_snap = list(self._strokes)  # estado atual = pré-erase para undo
            self._strokes = list(after_snap)   # reaplicar erase
            self._undo_ops.append(("erase", before_snap))
            self._canvas = None
            self._wb_canvas = None
            self._invalidate_erased_cache()
            self.update()

    def clear(self):
        self._strokes.clear()
        self._undo_stack.clear()
        self._undo_ops.clear()
        self._erase_scratch = None
        self._pen_scratch = None
        self._wb_canvas = None
        self._invalidate_erased_cache()
        if self._canvas is not None:
            self._canvas.fill(Qt.GlobalColor.transparent)
        self.update()

    def delete_stroke(self, idx: int):
        """Remove um stroke específico por índice. Suporta undo via Ctrl+Z."""
        if idx < 0 or idx >= len(self._strokes):
            return
        before = list(self._strokes)
        self._strokes.pop(idx)
        self._undo_ops.append(("erase", before))
        self._undo_stack.clear()
        self._canvas = None
        self._wb_canvas = None
        self._invalidate_erased_cache()
        self._drag_hover_idx = None
        self._drag_stroke_idx = None
        self.update()

    # ── Drag tool helpers ─────────────────────────────────────────────────────

    _DRAG_HANDLE_R = 12.0  # raio do círculo visual E da área de clique

    @staticmethod
    def _stroke_anchor(stroke) -> QPointF:
        """Retorna o ponto âncora do stroke (posição p/ texto, centroide p/ demais)."""
        if stroke[0][1].get("tool") == "text":
            p = stroke[0][0]
            return QPointF(p.x(), p.y())
        if stroke[0][1].get("tool") == "bitmap":
            p = stroke[0][0]
            px = stroke[0][1].get("pixmap")
            if px:
                return QPointF(p.x() + px.width() / 2.0, p.y() + px.height() / 2.0)
            return QPointF(p.x(), p.y())
        pts = [pt for pt, _ in stroke]
        return QPointF(
            sum(p.x() for p in pts) / len(pts),
            sum(p.y() for p in pts) / len(pts),
        )

    def _stroke_bbox(self, stroke: list) -> QRectF:
        """Bounding box do stroke em coords de canvas, incluindo largura do traço."""
        props = stroke[0][1]
        tool = props.get("tool", "pen")
        p0 = stroke[0][0]
        if tool == "bitmap":
            px = props.get("pixmap")
            if px is None:
                return QRectF(p0.x(), p0.y(), 0, 0)
            return QRectF(p0.x(), p0.y(), px.width(), px.height())
        if tool == "text":
            font = QFont(props.get("font_family", "Sans Serif"))
            font.setPointSizeF(max(1.0, float(props.get("size", 16))))
            fm = QFontMetrics(font)
            lines = props.get("text", "").split("\n")
            w = max((fm.horizontalAdvance(ln) for ln in lines), default=0)
            h = fm.height() * max(1, len(lines))
            return QRectF(p0.x(), p0.y() - fm.ascent(), w, h)
        xs = [pt.x() for pt, _ in stroke]
        ys = [pt.y() for pt, _ in stroke]
        sz = props.get("size", 3)
        if tool == "eraser":
            half = sz * 2.0
        elif tool == "highlighter":
            half = sz * 3.0
        else:
            half = sz / 2.0
        pad = half + 2.0  # margem anti-aliasing
        return QRectF(
            min(xs) - pad, min(ys) - pad,
            (max(xs) - min(xs)) + 2 * pad, (max(ys) - min(ys)) + 2 * pad,
        )

    def _build_drag_pixmap(self, indices: list[int]) -> None:
        """Pré-renderiza os strokes num pixmap flutuante para drag O(1) por frame.

        Se o bounding box for desproporcional (stroke gigante em canvas WB com
        muito pan), deixa _drag_pixmap = None — paintEvent usa o fallback que
        desenha o stroke com painter.translate(offset), ainda sem rebuild de pontos.
        """
        self._drag_pixmap = None
        rect = QRectF()
        for i in indices:
            if self._strokes[i]:
                rect = rect.united(self._stroke_bbox(self._strokes[i]))
        w = int(math.ceil(rect.width()))
        h = int(math.ceil(rect.height()))
        if w <= 0 or h <= 0:
            return
        # Cap de memória: até 4× a área do ecrã (~127MB em 4K). Acima disso, fallback.
        if w * h > self.width() * self.height() * 4:
            return
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(-rect.left(), -rect.top())
        for i in indices:
            self._draw_stroke(p, self._strokes[i])
        p.end()
        self._drag_pixmap = pm
        self._drag_pix_pos = QPointF(rect.left(), rect.top())

    def _find_linked_erasers(self, stroke_idx: int) -> list[int]:
        """Índices de strokes de borracha posteriores que cobrem stroke_idx.

        Um stroke de borracha é considerado 'ancorado' ao stroke_idx se qualquer
        ponto do traço de borracha está dentro do raio da borracha de qualquer
        ponto do stroke_idx. Estes erasers movem-se junto quando stroke_idx é arrastado.
        """
        stroke = self._strokes[stroke_idx]
        if not stroke:
            return []
        if stroke[0][1].get("tool") == "bitmap":
            return []  # bitmap já tem erasers baked in
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

    def _bake_stroke_with_erasers(self, stroke_idx: int, eraser_indices: list[int]) -> None:
        """Converte stroke + erasers ancorados num bitmap stroke independente.

        Renderiza stroke e erasers num QPixmap local (com SourceOver + Clear internos).
        O bitmap é depois composited com SourceOver no canvas principal — os buracos do
        eraser ficam transparentes no bitmap mas NÃO apagam pixels de outros strokes.
        """
        stroke = self._strokes[stroke_idx]
        if not stroke:
            return

        def _half_w(s: list) -> float:
            t = s[0][1].get("tool", "pen")
            sz = s[0][1].get("size", 3)
            if t == "eraser":     return sz * 2       # largura total = size*4
            if t == "highlighter": return sz * 3      # largura total = size*6
            return sz / 2.0

        all_pts = [(pt.x(), pt.y()) for pt, _ in stroke]
        pad = _half_w(stroke)
        for j in eraser_indices:
            s = self._strokes[j]
            all_pts.extend((pt.x(), pt.y()) for pt, _ in s)
            pad = max(pad, _half_w(s))
        pad += 2  # margem anti-aliasing

        min_x = max(0, int(min(p[0] for p in all_pts) - pad))
        min_y = max(0, int(min(p[1] for p in all_pts) - pad))
        max_x = min(self.width(),  int(max(p[0] for p in all_pts) + pad + 1))
        max_y = min(self.height(), int(max(p[1] for p in all_pts) + pad + 1))
        w, h = max_x - min_x, max_y - min_y
        if w <= 0 or h <= 0:
            return

        pixmap = QPixmap(w, h)
        pixmap.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pixmap)
        gp.setRenderHint(QPainter.RenderHint.Antialiasing)
        ox, oy = -min_x, -min_y

        def shifted(s: list) -> list:
            return [(QPointF(pt.x() + ox, pt.y() + oy), props) for pt, props in s]

        self._draw_stroke(gp, shifted(stroke))
        for j in eraser_indices:
            self._draw_stroke(gp, shifted(self._strokes[j]))
        gp.end()

        # Substitui o stroke pelo bitmap
        self._strokes[stroke_idx] = [(QPointF(min_x, min_y), {
            "tool":    "bitmap",
            "pixmap":  pixmap,
            "color":   QColor(0, 0, 0, 0),
            "size":    0,
        })]
        # Remove erasers do maior para o menor para não deslocar índices
        for j in sorted(eraser_indices, reverse=True):
            del self._strokes[j]
        self._canvas = None
        self._wb_canvas = None
        self._invalidate_erased_cache()

    def _invalidate_erased_cache(self) -> None:
        """Limpa os caches dependentes de _strokes (erased + âncoras).
        Chamar sempre que _strokes muda estruturalmente ou de geometria."""
        self._erased_cache.clear()
        self._anchor_cache.clear()

    def _anchor(self, idx: int) -> QPointF:
        """Âncora do stroke idx, cacheada (centroide é O(pontos) para calcular)."""
        a = self._anchor_cache.get(idx)
        if a is None:
            a = self._stroke_anchor(self._strokes[idx])
            self._anchor_cache[idx] = a
        return a

    def _is_fully_erased(self, idx: int) -> bool:
        """True se todos os pontos amostrados do stroke estão cobertos por erasers posteriores.
        Resultado cacheado por índice; válido até à próxima chamada a _invalidate_erased_cache."""
        cached = self._erased_cache.get(idx)
        if cached is not None:
            return cached
        result = self._compute_is_fully_erased(idx)
        self._erased_cache[idx] = result
        return result

    def _compute_is_fully_erased(self, idx: int) -> bool:
        """Cálculo real de _is_fully_erased, sem cache — O(pontos × erasers)."""
        stroke = self._strokes[idx]
        if not stroke:
            return True
        if stroke[0][1].get("tool") == "bitmap":
            return False  # bitmap: erasers já baked in, não rastreamos por pontos
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

    # ── Apagamento destrutivo ─────────────────────────────────────────────────

    def _apply_destructive_erase(self, eraser_stroke: list) -> None:
        """Aplica apagamento destrutivo: remove/divide strokes com base no traço da borracha.

        pen/highlighter      → divide em segmentos não cobertos pela borracha.
        line/rect/circle     → discretiza em pontos e divide da mesma forma;
                               resultado são segmentos pen com a mesma cor/size.
        text/bitmap          → remove inteiramente se tocado.
        eraser (legado WB)   → preservado sem alteração.

        Não adiciona a borracha a _strokes; atualiza diretamente a lista.
        """
        if not eraser_stroke:
            return
        eraser_size = eraser_stroke[0][1].get("size", 3)
        eraser_r    = eraser_size * 2          # raio = metade da largura do traço (size*4)
        eraser_r2   = eraser_r * eraser_r
        eraser_pts  = [(pt.x(), pt.y()) for pt, _ in eraser_stroke]

        new_strokes: list = []
        for stroke in self._strokes:
            if not stroke:
                continue
            tool = stroke[0][1].get("tool")
            if tool == "eraser":
                # Strokes de borracha legados (do WB) — preserva sem modificar
                new_strokes.append(stroke)
            elif tool in ("pen", "highlighter"):
                segs = self._split_stroke_destructive(stroke, eraser_pts, eraser_r2)
                new_strokes.extend(segs)
            elif tool in ("line", "rect", "circle"):
                # Forma geométrica: discretiza em pontos e aplica o mesmo split.
                # Se o eraser não tocar, mantém a forma original intacta.
                if self._eraser_hits_stroke(stroke, eraser_pts, eraser_r2):
                    disc = self._discretize_shape(stroke)
                    if disc:
                        segs = self._split_stroke_destructive(disc, eraser_pts, eraser_r2)
                        new_strokes.extend(segs)
                    # se disc vazio → forma removida totalmente (correto)
                else:
                    new_strokes.append(stroke)
            else:
                # text, bitmap: remove se tocado; mantém caso contrário
                if not self._eraser_hits_stroke(stroke, eraser_pts, eraser_r2):
                    new_strokes.append(stroke)

        self._strokes = new_strokes
        self._canvas = None
        self._wb_canvas = None
        self._invalidate_erased_cache()

    def _split_stroke_destructive(self, stroke: list,
                                   eraser_pts: list,
                                   eraser_r2: float) -> list:
        """Divide um stroke pen/highlighter nos segmentos não cobertos pela borracha.

        Pontos cujo centro está dentro de qualquer círculo da borracha são removidos.
        Segmentos contíguos de pontos não cobertos são retornados como strokes separados.
        Segmentos com 0 pontos são descartados; com 1+ pontos são mantidos (dot válido).
        """
        segments: list = []
        current: list  = []

        for pt, props in stroke:
            px, py = pt.x(), pt.y()
            covered = any((px - ex) ** 2 + (py - ey) ** 2 <= eraser_r2
                          for ex, ey in eraser_pts)
            if covered:
                if current:
                    segments.append(current)
                    current = []
            else:
                current.append((pt, props))

        if current:
            segments.append(current)
        return segments

    def _discretize_shape(self, stroke: list) -> list:
        """Converte um stroke line/rect/circle em pontos amostrados com props 'pen'.

        Retorna uma lista de (QPointF, props_dict) que pode ser passada directamente
        a _split_stroke_destructive. O tool é mapeado para 'pen' para que o resultado
        seja renderizável como traço contínuo. Cor e size são preservados.

        N de pontos: proporcional ao perímetro estimado da forma (≥ 1 pt / 2 px).
        """
        if not stroke:
            return []
        tool  = stroke[0][1].get("tool")
        props = stroke[0][1]
        color = props.get("color", QColor("#FF0000"))
        size  = props.get("size", 3)
        pen_props = {"tool": "pen", "color": QColor(color), "size": size}

        pts = [pt for pt, _ in stroke]
        if len(pts) < 2:
            return [(QPointF(pts[0]), pen_props)]
        p0, p1 = pts[0], pts[-1]
        x0, y0 = p0.x(), p0.y()
        x1, y1 = p1.x(), p1.y()

        result: list[tuple] = []

        if tool == "line":
            length = math.hypot(x1 - x0, y1 - y0)
            N = max(2, int(length / 2))
            for i in range(N + 1):
                t = i / N
                result.append((QPointF(x0 + t * (x1 - x0),
                                       y0 + t * (y1 - y0)), pen_props))

        elif tool == "rect":
            # Normaliza para garantir x0 < x1, y0 < y1
            lx, rx = min(x0, x1), max(x0, x1)
            ty, by = min(y0, y1), max(y0, y1)
            w, h = rx - lx, by - ty
            perimeter = 2 * (w + h)
            N = max(4, int(perimeter / 2))
            # Distribui pontos proporcionalmente por cada lado
            sides = [
                (lx, ty, rx, ty),   # topo (esq→dir)
                (rx, ty, rx, by),   # direita (cima→baixo)
                (rx, by, lx, by),   # fundo (dir→esq)
                (lx, by, lx, ty),   # esquerda (baixo→cima)
            ]
            for sx0, sy0, sx1, sy1 in sides:
                side_len = math.hypot(sx1 - sx0, sy1 - sy0)
                n_side = max(1, int(N * side_len / perimeter))
                for i in range(n_side):
                    t = i / n_side
                    result.append((QPointF(sx0 + t * (sx1 - sx0),
                                           sy0 + t * (sy1 - sy0)), pen_props))
            # Fecha o rectângulo (último ponto = primeiro)
            result.append((QPointF(lx, ty), pen_props))

        elif tool == "circle":
            cx_e = (x0 + x1) / 2.0
            cy_e = (y0 + y1) / 2.0
            rx   = abs(x1 - x0) / 2.0
            ry   = abs(y1 - y0) / 2.0
            # Aproximação do perímetro pela fórmula de Ramanujan
            a, b = max(rx, ry), min(rx, ry)
            h_ram = ((a - b) / (a + b)) ** 2 if (a + b) > 0 else 0
            perimeter = math.pi * (a + b) * (1 + 3 * h_ram / (10 + math.sqrt(4 - 3 * h_ram)))
            N = max(12, int(perimeter / 2))
            for i in range(N + 1):
                angle = 2.0 * math.pi * i / N
                result.append((QPointF(cx_e + rx * math.cos(angle),
                                       cy_e + ry * math.sin(angle)), pen_props))

        return result

    def _eraser_hits_stroke(self, stroke: list,
                             eraser_pts: list,
                             eraser_r2: float) -> bool:
        """Retorna True se o eraser toca em qualquer parte do stroke.

        Para pen/highlighter verifica os pontos armazenados.
        Para line/rect/circle amostra a geometria — necessário porque só há 2 pontos
        gravados (início e fim) e o eraser pode cruzar o meio sem cobrir as extremidades.
        Para text usa o ponto âncora.
        Para bitmap verifica a bounding box.
        """
        if not stroke:
            return False
        tool = stroke[0][1].get("tool")

        # ── text: bounding box calculada via QFontMetrics ────────────────────
        if tool == "text":
            props = stroke[0][1]
            pt = stroke[0][0]
            font = QFont(props.get("font_family", "Sans Serif"))
            font.setPointSizeF(max(1.0, float(props.get("size", 16))))
            fm = QFontMetrics(font)
            lines = props.get("text", "").split("\n")
            bw = max((fm.horizontalAdvance(l) for l in lines), default=fm.horizontalAdvance(" "))
            bh = fm.height() * len(lines)
            # drawText usa baseline como Y: topo real = pt.y() - ascent
            bx = pt.x()
            by = pt.y() - fm.ascent()
            eraser_r = eraser_r2 ** 0.5
            return any(
                bx - eraser_r <= ex <= bx + bw + eraser_r and
                by - eraser_r <= ey <= by + bh + eraser_r
                for ex, ey in eraser_pts
            )

        # ── bitmap: verifica bounding box ────────────────────────────────────
        if tool == "bitmap":
            origin = stroke[0][0]
            px_obj = stroke[0][1].get("pixmap")
            if px_obj is None:
                return False
            bx, by = origin.x(), origin.y()
            bw, bh = px_obj.width(), px_obj.height()
            # Ponto do eraser dentro do retângulo do bitmap?
            for ex, ey in eraser_pts:
                if bx <= ex <= bx + bw and by <= ey <= by + bh:
                    return True
            # Círculo do eraser toca a borda (aproximação pelos 4 cantos)?
            er = math.sqrt(eraser_r2)
            if any(bx - er <= ex <= bx + bw + er and by - er <= ey <= by + bh + er
                   for ex, ey in eraser_pts):
                return True
            return False

        # ── line/rect/circle: amostra a geometria ────────────────────────────
        if tool in ("line", "rect", "circle"):
            pts = [pt for pt, _ in stroke]
            if len(pts) < 2:
                pt = pts[0]
                return any((pt.x() - ex) ** 2 + (pt.y() - ey) ** 2 <= eraser_r2
                           for ex, ey in eraser_pts)
            p0, p1 = pts[0], pts[-1]
            x0, y0 = p0.x(), p0.y()
            x1, y1 = p1.x(), p1.y()

            samples: list[tuple[float, float]] = []
            N = 24
            if tool == "line":
                for i in range(N + 1):
                    t = i / N
                    samples.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
            elif tool == "rect":
                for i in range(N + 1):
                    t = i / N
                    samples.append((x0 + t * (x1 - x0), y0))           # topo
                    samples.append((x0 + t * (x1 - x0), y1))           # fundo
                    samples.append((x0,                  y0 + t * (y1 - y0)))  # esquerda
                    samples.append((x1,                  y0 + t * (y1 - y0)))  # direita
            elif tool == "circle":
                cx_e = (x0 + x1) / 2.0
                cy_e = (y0 + y1) / 2.0
                rx = abs(x1 - x0) / 2.0
                ry = abs(y1 - y0) / 2.0
                for i in range(N + 1):
                    angle = 2.0 * math.pi * i / N
                    samples.append((cx_e + rx * math.cos(angle),
                                    cy_e + ry * math.sin(angle)))

            for sx, sy in samples:
                for ex, ey in eraser_pts:
                    if (sx - ex) ** 2 + (sy - ey) ** 2 <= eraser_r2:
                        return True
            return False

        # ── pen/highlighter: verifica pontos armazenados ─────────────────────
        for pt, _ in stroke:
            px, py = pt.x(), pt.y()
            for ex, ey in eraser_pts:
                if (px - ex) ** 2 + (py - ey) ** 2 <= eraser_r2:
                    return True
        return False

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
            a = self._anchor(i)
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
        if stroke[0][1].get("tool") == "bitmap":
            return  # bitmap não é escalável (renderização pré-fixada)
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
        self._wb_canvas = None
        self._invalidate_erased_cache()  # geometria mudou — âncoras/erased inválidos
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

        # Botão direito com ferramenta de criação: Limpar a tela
        # (ignorado sobre a toolbar embutida — lá o clique é da UI)
        if (event.button() == Qt.MouseButton.RightButton and self._active
                and not self._drawing
                and self._tool in ("pen", "highlighter", "line",
                                   "rect", "circle", "text")):
            tb = self._toolbar_widget
            if tb is None or not tb.geometry().contains(event.pos()):
                self.clear()
                event.accept()
                return

        if not self._active or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._tool == "laser":
            return
        if self._tool == "drag":
            idx = self._find_stroke_at(event.pos())
            if idx is not None:
                # Encontra erasers ligados ao stroke (antes de qualquer modificação)
                linked = self._find_linked_erasers(idx)
                if not self._whiteboard:
                    # Não-WB: bake → erasers removidos e stroke vira bitmap
                    if linked:
                        self._bake_stroke_with_erasers(idx, linked)
                    linked = []  # erasers já baked in bitmap, não precisamos deles
                # else (WB): linked mantém-se para movimento coordenado e exclusão da base

                # Traz o stroke para o topo da z-order (fica acima de erasers globais)
                if idx < len(self._strokes) - 1:
                    stroke_item = self._strokes[idx]
                    del self._strokes[idx]
                    # Ajusta índices dos erasers ligados: deleção de idx desloca j>idx em -1
                    linked = [j - 1 if j > idx else j for j in linked]
                    self._strokes.append(stroke_item)
                    idx = len(self._strokes) - 1
                    self._canvas = None
                    self._wb_canvas = None
                    self._invalidate_erased_cache()

                # Constrói _drag_base: todos os strokes EXCEPTO arrastado E erasers ligados.
                # Elimina _rebuild_canvas por frame → O(1) por frame durante o drag.
                drag_idx = idx
                linked_set = set(linked)
                self._drag_base = QPixmap(self.size())
                if self._whiteboard:
                    self._drag_base.fill(self._wb_bg)
                    p = QPainter(self._drag_base)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing)
                    p.translate(self._wb_pan)
                    p.scale(self._wb_zoom, self._wb_zoom)
                else:
                    self._ensure_canvas()
                    self._drag_base.fill(Qt.GlobalColor.transparent)
                    p = QPainter(self._drag_base)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing)
                for i, s in enumerate(self._strokes):
                    if i != drag_idx and i not in linked_set:
                        self._draw_stroke(p, s)
                p.end()
                self._drag_stroke_idx = idx
                self._drag_linked_erasers = linked  # WB: move junto; não-WB: []
                self._drag_last_pos = event.pos()
                self._drag_offset = QPointF(0.0, 0.0)
                # Stroke arrastado vira um pixmap flutuante: o move só altera o
                # offset (dois blits por frame, custo independente dos pontos).
                self._build_drag_pixmap([idx] + linked)
                self._drawing = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self._tool == "text":
            self.text_placement_requested.emit(event.pos())
            return
        self._drawing = True
        self._current_stroke = [(self._to_canvas(event.pos()), self._brush_props())]
        self._undo_stack.clear()
        if self._tool == "eraser":
            # Apagamento destrutivo ao vivo (WB e não-WB).
            # Salva snapshot para undo e aplica o ponto de press imediatamente.
            self._erase_base = list(self._strokes)
            self._apply_destructive_erase(self._current_stroke)
            # _apply_destructive_erase já invalida _canvas e _wb_canvas
        elif not self._whiteboard:
            self._ensure_canvas()
            if self._tool in ("pen", "highlighter"):
                # Layer transparente separado do canvas — evita GPU COW deep-copy por stroke.
                # paintEvent blit canvas + pen_scratch; release usa _commit_stroke O(pts).
                self._pen_scratch = QPixmap(self.size())
                self._pen_scratch.fill(Qt.GlobalColor.transparent)
                p = QPainter(self._pen_scratch)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_stroke(p, self._current_stroke)
                p.end()

    def mouseMoveEvent(self, event):
        pos = event.pos()
        self.cursor_moved.emit(pos)

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
                # Só acumula o offset — nenhum ponto é transladado por frame.
                # A translação real acontece uma única vez no mouseRelease.
                self._drag_offset = QPointF(
                    self._drag_offset.x() + dx, self._drag_offset.y() + dy,
                )
                self._drag_last_pos = pos
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
        new_pt = self._to_canvas(pos)

        # Decimação de pontos: ignora movimentos menores que o limiar mínimo.
        # Evita strokes com milhares de pontos (lentidão em _rebuild_canvas,
        # _is_fully_erased e _find_linked_erasers) e reduz chamadas QPainter.
        if self._current_stroke:
            last_pt = self._current_stroke[-1][0]
            dx = new_pt.x() - last_pt.x()
            dy = new_pt.y() - last_pt.y()
            # Borracha: mínimo = tamanho do raio (eraser já é largo, pontos densos desnecessários)
            # Demais ferramentas: 2px — suavidade visual suficiente
            min_d2 = float(self._size) ** 2 if self._tool == "eraser" else 4.0
            if dx * dx + dy * dy < min_d2:
                return  # ponto desnecessário → sem update

        self._current_stroke.append((new_pt, self._brush_props()))

        if self._tool == "eraser":
            # Apagamento ao vivo (WB e não-WB): aplica apenas o novo ponto.
            self._apply_destructive_erase(self._current_stroke[-1:])
            # _apply_destructive_erase já invalida _canvas e _wb_canvas
        else:
            # pen/highlighter: aplica só o novo segmento no pen_scratch — O(1) por frame
            pts = self._current_stroke
            if len(pts) >= 2 and self._pen_scratch is not None:
                p = QPainter(self._pen_scratch)
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
                # Aplica o offset acumulado aos pontos UMA vez (não por frame)
                off = self._drag_offset
                if self._drag_stroke_idx is not None and (off.x() or off.y()):
                    self._translate_stroke(self._drag_stroke_idx, off)
                    for j in self._drag_linked_erasers:
                        if j < len(self._strokes):
                            self._translate_stroke(j, off)
                self._drawing = False
                self._drag_stroke_idx = None
                self._drag_linked_erasers = []
                self._drag_last_pos = None
                self._drag_base = None
                self._drag_pixmap = None
                self._drag_offset = QPointF(0.0, 0.0)
                # Rebuild de canvas uma vez no final do drag (não por frame)
                self._canvas = None
                self._wb_canvas = None
                self._invalidate_erased_cache()  # posições mudaram durante o drag
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self._tool == "laser" or not self._drawing:
            return
        self._drawing = False
        if self._tool == "eraser":
            # Erase ao vivo já aplicado (WB e não-WB). Só grava o undo.
            if self._erase_base is not None:
                self._undo_ops.append(("erase", self._erase_base))
                self._erase_base = None
        elif self._current_stroke:
            stroke = list(self._current_stroke)
            # ── Stroke normal (pen, highlighter, line, rect, circle, eraser WB) ──
            self._strokes.append(stroke)
            self._undo_ops.append(("add",))
            self._invalidate_erased_cache()
            if self._whiteboard:
                if self._wb_canvas is not None:
                    wb_p = QPainter(self._wb_canvas)
                    wb_p.setRenderHint(QPainter.RenderHint.Antialiasing)
                    wb_p.translate(self._wb_pan)
                    wb_p.scale(self._wb_zoom, self._wb_zoom)
                    self._draw_stroke(wb_p, stroke)
                    wb_p.end()
            elif self._pen_scratch is not None:
                self._pen_scratch = None
                self._ensure_canvas()
                self._commit_stroke(stroke)
            else:
                self._ensure_canvas()
                self._commit_stroke(stroke)
        self._current_stroke = []
        self.update()

    def mouseDoubleClickEvent(self, event):
        if (self._tool == "drag"
                and event.button() == Qt.MouseButton.LeftButton):
            idx = self._find_stroke_at(event.pos())
            if idx is not None and self._strokes[idx][0][1].get("tool") == "text":
                self.text_edit_requested.emit(idx)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

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
            self._wb_canvas = None  # pan ou zoom mudou — WB precisa rebuild
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
                if self._drawing and self._drag_stroke_idx is not None:
                    # Pixmap flutuante reflete o stroke escalado
                    self._build_drag_pixmap(
                        [self._drag_stroke_idx] + self._drag_linked_erasers
                    )
            event.accept()
        else:
            event.ignore()

    # ── Painting ──────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._erase_scratch = None
        self._pen_scratch = None
        self._drag_base = None
        self._canvas = None
        self._wb_canvas = None
        self._invalidate_erased_cache()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ── Drag activo: blit base + blit pixmap flutuante no offset — O(1) ──────
        if self._drag_base is not None and self._drawing and self._drag_stroke_idx is not None:
            painter.drawPixmap(0, 0, self._drag_base)
            off = self._drag_offset
            if self._whiteboard:
                painter.save()
                painter.translate(self._wb_pan)
                painter.scale(self._wb_zoom, self._wb_zoom)
            if self._drag_pixmap is not None:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawPixmap(
                    QPointF(self._drag_pix_pos.x() + off.x(),
                            self._drag_pix_pos.y() + off.y()),
                    self._drag_pixmap,
                )
            else:
                # Fallback (bbox gigante): desenha com translate — os pontos do
                # stroke continuam intactos, só a transform do painter muda.
                painter.save()
                painter.translate(off)
                self._draw_stroke(painter, self._strokes[self._drag_stroke_idx])
                for j in self._drag_linked_erasers:
                    if j < len(self._strokes):
                        self._draw_stroke(painter, self._strokes[j])
                painter.restore()
            self._draw_drag_handles(painter)
            if self._whiteboard:
                painter.restore()
        # ── Restante (não drag activo) ────────────────────────────────────────────
        elif self._whiteboard:
            # Cache WB: rebuild apenas quando strokes/pan/zoom/tamanho mudam.
            # Frames normais (laser, spotlight, hover) são um drawPixmap O(1).
            if self._wb_canvas is None or self._wb_canvas.size() != self.size():
                self._wb_canvas = QPixmap(self.size())
                self._wb_canvas.fill(self._wb_bg)
                wb_p = QPainter(self._wb_canvas)
                wb_p.setRenderHint(QPainter.RenderHint.Antialiasing)
                wb_p.translate(self._wb_pan)
                wb_p.scale(self._wb_zoom, self._wb_zoom)
                for stroke in self._strokes:
                    self._draw_stroke(wb_p, stroke)
                wb_p.end()
            painter.drawPixmap(0, 0, self._wb_canvas)
            # Stroke activo desenhado directamente sobre o painter já com WB blit.
            # Eraser não é desenhado: _wb_canvas já reflecte os strokes modificados.
            if self._current_stroke and self._tool != "eraser":
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.translate(self._wb_pan)
                painter.scale(self._wb_zoom, self._wb_zoom)
                self._draw_stroke(painter, self._current_stroke)
                painter.restore()
            if self._tool == "drag" and self._strokes:
                # Handles em WB precisam da mesma transform que os strokes
                painter.save()
                painter.translate(self._wb_pan)
                painter.scale(self._wb_zoom, self._wb_zoom)
                self._draw_drag_handles(painter)
                painter.restore()
        else:
            self._ensure_canvas()
            if self._pen_scratch is not None:
                # pen_scratch é layer transparente — blit canvas base primeiro
                painter.drawPixmap(0, 0, self._canvas)
                painter.drawPixmap(0, 0, self._pen_scratch)
            else:
                painter.drawPixmap(0, 0, self._canvas)
                # Eraser ao vivo: _current_stroke não deve ser desenhado —
                # _canvas já reflecte os strokes modificados.
                if self._current_stroke and self._tool != "eraser":
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

        # Bitmap stroke: QPixmap pré-renderizado, composited com SourceOver
        if tool == "bitmap":
            px = props.get("pixmap")
            if px is not None:
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceOver)
                anchor = stroke[0][0]
                painter.drawPixmap(int(anchor.x()), int(anchor.y()), px)
            return

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
            font = QFont(props.get("font_family", "Sans Serif"))
            font.setPointSizeF(max(1.0, float(props.get("size", 16))))
            painter.setFont(font)
            painter.setPen(QPen(color))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            lines = props.get("text", "").split("\n")
            fm = painter.fontMetrics()
            line_h = fm.height()
            origin = raw[0]
            for i, line in enumerate(lines):
                painter.drawText(QPointF(origin.x(), origin.y() + i * line_h), line)
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

            anchor = self._anchor(i)
            if self._drawing and i == self._drag_stroke_idx:
                # Pontos só são transladados no release — handle segue o offset
                anchor = QPointF(
                    anchor.x() + self._drag_offset.x(),
                    anchor.y() + self._drag_offset.y(),
                )

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
            or self._magnifier_tracking
        )

    def set_magnifier_tracking(self, on: bool):
        """Lupa ativa: liga o hover tracking para alimentar cursor_moved."""
        self._magnifier_tracking = on
        self._update_tracking()

#!/usr/bin/env python3
import os as _os
import sys

# ── Telemetria de crash ───────────────────────────────────────────────────────
# Segfault nativo (troca de monitor, superfície layer-shell, QScreen pendente)
# não deixa traceback Python — o log ficava mudo. faulthandler despeja a stack
# de TODAS as threads em SIGSEGV/SIGABRT/SIGFPE direto no stderr (→ log).
import faulthandler as _faulthandler
_faulthandler.enable(all_threads=True)

# Exceção não tratada num slot/virtual faz o PyQt chamar qFatal() e encerrar
# em silêncio. Um excepthook próprio garante que o traceback chegue ao log.
def _log_excepthook(exc_type, exc, tb):
    import traceback
    traceback.print_exception(exc_type, exc, tb)
    sys.stderr.flush()

sys.excepthook = _log_excepthook

import instance_guard
if not instance_guard.acquire():
    print("[epicpen] Já existe uma instância em execução.", file=sys.stderr)
    sys.exit(0)

# GNOME Wayland não suporta wlr-layer-shell nem honra WindowStaysOnTopHint
# para xdg_toplevel nativo. QT_QPA_PLATFORM=xcb força XWayland; o Mutter
# honra _NET_WM_STATE_ABOVE (mapeado de WindowStaysOnTopHint) para clientes XWayland.
_xdg = _os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
if ("gnome" in _xdg
        and _os.environ.get("WAYLAND_DISPLAY")
        and _os.environ.get("QT_QPA_PLATFORM", "wayland") != "xcb"):
    _os.environ["QT_QPA_PLATFORM"] = "xcb"
    print("[gnome] GNOME Wayland detectado — usando XWayland (QT_QPA_PLATFORM=xcb)")

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction


def _show_about(overlay):
    """Exibe modal 'Sobre' pausando o overlay para o dialog receber cliques."""
    was_active = getattr(overlay, '_active', False)
    if was_active:
        overlay.set_active(False)
    name    = QApplication.applicationName()
    version = QApplication.applicationVersion()
    dlg = QMessageBox()
    dlg.setWindowTitle(f"Sobre {name}")
    dlg.setText(f"<b>{name}</b>")
    dlg.setInformativeText(
        f"Versão: <b>{version}</b><br><br>"
        "Clone Linux do EpicPen para desenho em tela,<br>"
        "com suporte nativo a Wayland e X11."
    )
    dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
    dlg.exec()
    if was_active:
        overlay.set_active(True)

import config as cfg
from overlay import OverlayWindow
from toolbar import ToolbarWindow
from tray import TrayIcon
import keepabove
import layershell


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("EpicPen")
    app.setApplicationVersion("1.0.12")
    app.setQuitOnLastWindowClosed(False)

    settings = cfg.load()

    # KDE Wayland: autoriza a captura silenciosa via KWin ScreenShot2
    # (lupa/screenshot sem diálogo de portal). Só tem efeito no AppImage.
    import kwinshot
    kwinshot.ensure_authorization()

    overlay = OverlayWindow()
    toolbar = ToolbarWindow(overlay, config=settings)
    tray    = TrayIcon(overlay, toolbar, app, config=settings)
    toolbar.set_tray(tray)

    # Insere "Sobre" antes de "Sair" no menu da tray (sem modificar tray.py)
    _tray_menu = tray._menu
    _quit_act  = _tray_menu.actions()[-1]          # "Sair" é sempre o último
    _act_about = QAction("ℹ️  Sobre", _tray_menu)
    _act_about.triggered.connect(lambda: _show_about(overlay))
    _tray_menu.insertAction(_quit_act, _act_about)

    from PyQt6.QtWidgets import QApplication as _App
    from PyQt6.QtCore import QPoint as _QPoint
    _primary = _App.primaryScreen()

    # Determina o monitor da toolbar ANTES de criar a superfície layer-shell do overlay,
    # para que overlay e toolbar sempre abram no mesmo output (monitor).
    toolbar.adjustSize()
    print(f"[toolbar] size antes de apply(): {toolbar.size().width()}×{toolbar.size().height()}")
    _tb_pos = settings.get("toolbar_pos", {"x": 20, "y": 150})
    _tb_x, _tb_y = _tb_pos.get("x", 20), _tb_pos.get("y", 150)

    # Encontra o monitor ao qual a posição salva pertence E onde o toolbar inteiro cabe.
    # Verifica top-left E bottom-right — evita toolbar parcialmente fora do ecrã.
    _tb_abs = _QPoint(_tb_x, _tb_y)
    _tb_w = toolbar.width() or 56
    _tb_h = toolbar.height() or 400
    _tb_screen = next(
        (s for s in QApplication.screens()
         if s.geometry().contains(_tb_abs)
         and _tb_x + _tb_w <= s.geometry().x() + s.geometry().width()
         and _tb_y + _tb_h <= s.geometry().y() + s.geometry().height()),
        None,
    )
    if _tb_screen is None:
        print(f"[toolbar] posição salva ({_tb_x},{_tb_y}+{_tb_w}×{_tb_h}) não cabe em nenhum monitor — resetando para (20,150)")
        _tb_x, _tb_y = 20, 150
        _tb_screen = _primary
        _tb_abs = _QPoint(_tb_x, _tb_y)

    # overlay → Layer::Top, 4-anchor + ExclusiveZone=0, no mesmo monitor que a toolbar.
    # compositor dimensiona a janela para a área disponível (exclui painel KDE).
    _lsw_o = layershell.apply(
        overlay,
        layer=layershell.LAYER_TOP,
        anchors=(layershell.ANCHOR_TOP | layershell.ANCHOR_BOTTOM |
                 layershell.ANCHOR_LEFT | layershell.ANCHOR_RIGHT),
        exclusive_zone=0,
        initial_pos=(0, 0),
        screen=_tb_screen,
    )

    # Margens layer-shell são relativas ao output (monitor), não absolutas.
    _tb_origin = _tb_screen.geometry().topLeft()
    _tb_rel_x  = _tb_x - _tb_origin.x()
    _tb_rel_y  = _tb_y - _tb_origin.y()
    print(f"[toolbar] screen={_tb_screen.name()} origin={_tb_origin.x()},{_tb_origin.y()} abs=({_tb_x},{_tb_y}) rel=({_tb_rel_x},{_tb_rel_y})")

    _lsw_t = layershell.apply(
        toolbar,
        layer=layershell.LAYER_TOP,
        exclusive_zone=-1,
        initial_pos=(_tb_rel_x, _tb_rel_y),
        screen=_tb_screen,
    )
    toolbar._lsw_ptr = _lsw_t
    toolbar._lsw_pos = _tb_abs  # coordenadas absolutas — referência interna

    # Lupa → Layer::Overlay no mesmo monitor. Janela comum não se move
    # sozinha no Wayland (move() é ignorado pelo compositor); com layer-shell
    # ela segue o cursor via margens (move_to). O apply acontece dentro da
    # lupa, na 1ª ativação — nunca no arranque (ver magnifier.py).
    if _lsw_o:
        toolbar._magnifier.enable_layershell(_tb_screen)

    # Ao sair: descarrega o script de cursor do KWin e o helper de captura
    app.aboutToQuit.connect(lambda: toolbar._magnifier.set_active(False))

    overlay._layer_shell_active = bool(_lsw_o)
    overlay._lsw_ptr = _lsw_o   # ponteiro para mudança dinâmica de layer

    # Wayland sem wlr-layer-shell (GNOME): embute a toolbar no overlay para evitar
    # z-ordering entre janelas separadas (apps aparecem entre toolbar e overlay).
    _use_embed = not _lsw_o and layershell.IS_WAYLAND

    if _lsw_o and _lsw_t:
        # Quando o overlay é remapeado (set_active True após estar escondido), ele fica
        # acima do toolbar na z-order de Layer::Top. Remapear o toolbar logo depois
        # devolve-o ao topo. O hide+show sincronos são processados no mesmo frame pelo KWin.
        # Guarda durante drag: evita piscagem causada pelo hide+show enquanto a toolbar se move.
        def _reraise_toolbar():
            if toolbar._dragging:
                return
            toolbar.hide()
            toolbar.show()
        overlay._on_remapped = _reraise_toolbar
        print("[layershell] overlay Layer::Top 4-anchor ExclusiveZone=0 + toolbar Layer::Overlay")
    elif _lsw_o:
        print("[layershell] overlay Layer::Top (toolbar sem layer-shell)")
    elif _use_embed:
        # embed_toolbar() deve ser chamado ANTES de overlay.show()
        overlay.embed_toolbar(toolbar)
        print("[layershell] GNOME Wayland — toolbar embutida no overlay (modo embed)")
    else:
        print("[layershell] FALHOU — usando fallback keepAbove (X11)")

    overlay.show()
    if not _use_embed:
        if not _lsw_t:
            toolbar.move(_tb_abs)
        toolbar.show()

    if not _lsw_o and not _use_embed:
        # Fallback keepAbove para sessões X11
        QTimer.singleShot(600, keepabove.set_keepabove)
        _ka_timer = QTimer()
        _ka_timer.setSingleShot(True)
        _ka_timer.setInterval(400)
        _ka_timer.timeout.connect(keepabove.set_keepabove)
        from PyQt6.QtGui import QGuiApplication
        QGuiApplication.instance().focusWindowChanged.connect(
            lambda _: _ka_timer.start()
        )

    # Arranca já em modo apresentação: toolbar auto-oculta com reveal na
    # borda e sobe para Layer::Overlay (acima de apps fullscreen). O
    # singleShot deixa as superfícies mapearem antes do set_layer — mesmo
    # caminho do F11 em runtime.
    def _start_presentation():
        toolbar._btn_present.setChecked(True)
        toolbar._toggle_presentation(True)
    QTimer.singleShot(300, _start_presentation)

    # Iniciar oculto se a opção estiver activa
    if settings.get("start_hidden", False):
        tray._toggle_visibility()

    def _full_state() -> dict:
        return {**toolbar.get_state(), **tray.get_state()}

    # Autosave com debounce de 500 ms para não escrever em disco a cada evento
    _save_timer = QTimer()
    _save_timer.setSingleShot(True)
    _save_timer.setInterval(500)
    _save_timer.timeout.connect(lambda: cfg.save(_full_state()))

    def schedule_save():
        _save_timer.start()

    # Conecta mudanças que precisam ser persistidas
    toolbar._size_slider.valueChanged.connect(lambda _: schedule_save())
    toolbar._zoom_slider.valueChanged.connect(lambda _: schedule_save())
    toolbar._btn_whiteboard.toggled.connect(lambda _: schedule_save())
    toolbar._color_btn.clicked.connect(lambda: schedule_save())
    for b in toolbar._tool_buttons:
        b.clicked.connect(lambda _: schedule_save())
    tray._act_start_hidden.triggered.connect(lambda _: schedule_save())

    # Salva posição da toolbar ao mover
    _move_timer = QTimer()
    _move_timer.setSingleShot(True)
    _move_timer.setInterval(800)
    _move_timer.timeout.connect(lambda: cfg.save(_full_state()))

    original_release = toolbar.mouseReleaseEvent

    def _on_release(event):
        original_release(event)
        _move_timer.start()

    toolbar.mouseReleaseEvent = _on_release

    # Salva ao fechar
    app.aboutToQuit.connect(lambda: cfg.save(_full_state()))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

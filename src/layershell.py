"""
Applies wlr-layer-shell surface type to a QWidget via LibLayerShellQt ctypes bindings.

Must be called BEFORE widget.show(). The shell surface type is baked in when Qt
creates the Wayland surface on the first show(). Calling after show() has no effect.

Enum values (from LayerShellQtQml.qmltypes + wlr-layer-shell-unstable-v1.xml):
  Layer:  Background=0  Bottom=1  Top=2  Overlay=3
  Anchor: None=0  Top=1  Bottom=2  Left=4  Right=8  (bit flags, combinable)
  KeyboardInteractivity: None=0  Exclusive=1  OnDemand=2

Layer::Top places the surface above all normal application windows, including
fullscreen windows, but below system overlays (lock screen, notifications).

Position is set via ANCHOR_TOP | ANCHOR_LEFT + margins (left=x, top=y).
ExclusiveZone=-1 allows the surface to overlap other layer-shell surfaces.
Call move_to() to reposition an already-shown layer-shell window.
"""
import ctypes
import os

IS_WAYLAND = (
    os.environ.get("WAYLAND_DISPLAY") is not None
    and os.environ.get("QT_QPA_PLATFORM", "wayland") != "xcb"
)

# GNOME (Mutter) não suporta wlr-layer-shell nativamente.
# Sem detecção, apply() devolve um ponteiro válido mas move_to() não move a janela,
# fazendo _lsw_pos derivar e a toolbar ir para as extremidades do ecrã ao arrastar.
_DESKTOP = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
IS_LAYERSHELL_COMPOSITOR = IS_WAYLAND and "gnome" not in _DESKTOP

LAYER_BACKGROUND = 0
LAYER_BOTTOM     = 1
LAYER_TOP        = 2
LAYER_OVERLAY    = 3

ANCHOR_NONE   = 0
ANCHOR_TOP    = 1
ANCHOR_BOTTOM = 2
ANCHOR_LEFT   = 4
ANCHOR_RIGHT  = 8

KBD_NONE      = 0
KBD_EXCLUSIVE = 1
KBD_ON_DEMAND = 2

# ScreenConfiguration: ScreenFromQWindow=0 faz a superfície seguir QWindow::screen()
# automaticamente — chamada setScreen() no QWindow move a superfície para o novo output.
SCREEN_FROM_QWINDOW    = 0
SCREEN_FROM_COMPOSITOR = 1

_LIB_SYSTEM = "/usr/lib64/libLayerShellQtInterface.so.6"
# Cópia local com versioning WEAK para compatibilidade com pip PyQt6
_LIB_LOCAL  = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "lib", "libLayerShellQtInterface.so.6")
_LIB_PATH   = _LIB_LOCAL if os.path.exists(_LIB_LOCAL) else _LIB_SYSTEM
_lib = None

# ctypes struct for const QMargins& — layout: left, top, right, bottom (4 × int)
class _QMargins(ctypes.Structure):
    _fields_ = [("m_left",   ctypes.c_int),
                ("m_top",    ctypes.c_int),
                ("m_right",  ctypes.c_int),
                ("m_bottom", ctypes.c_int)]

# ctypes struct for const QSize& — layout: width (wd), height (ht) (2 × int)
class _QSize(ctypes.Structure):
    _fields_ = [("wd", ctypes.c_int), ("ht", ctypes.c_int)]


def _get_lib():
    global _lib
    if _lib is not None:
        return _lib
    try:
        _lib = ctypes.CDLL(_LIB_PATH)
        print(f"[layershell] ctypes.CDLL OK: {_LIB_PATH}")
    except OSError as e:
        print(f"[layershell] ctypes.CDLL ERRO: {e}")
    return _lib


def apply(widget,
          layer:          int            = LAYER_TOP,
          anchors:        int            = ANCHOR_TOP | ANCHOR_LEFT,
          kbd:            int            = KBD_NONE,
          exclusive_zone: int            = -1,
          initial_pos:    "tuple | None" = None,
          screen                         = None) -> "int | None":
    """
    Attach a LayerShellQt::Window to *widget* before its Wayland surface is created.

    exclusive_zone: -1 = sobrepõe zonas exclusivas (painel); 0 = respeita o painel.
    initial_pos: (x, y) explícito; se None usa widget.pos() (pode ser lixo no Wayland).
    screen: QScreen para associar ao output Wayland correto (evita monitor errado).
    Returns the raw LayerShellQt::Window* (as int) on success, None on failure.
    """
    print(f"[layershell] apply() WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')!r}"
          f" QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM')!r} IS_WAYLAND={IS_WAYLAND}")
    if not IS_WAYLAND:
        print("[layershell] ambiente não é Wayland nativo — abortando")
        return None
    if not IS_LAYERSHELL_COMPOSITOR:
        print(f"[layershell] compositor não suporta wlr-layer-shell (GNOME?) — desativado")
        return None

    lib = _get_lib()
    if lib is None:
        print(f"[layershell] falha ao carregar {_LIB_PATH}")
        return None
    print(f"[layershell] biblioteca carregada: {_LIB_PATH}")

    try:
        import PyQt6.sip as sip

        # winId() cria o QWindow (objeto Qt), mas NÃO o QWaylandWindow (platform window).
        # Window::get() chama window->handle() internamente; sem platform window ele adia
        # para visibleChanged e o Wayland acaba criando xdg_toplevel em vez de layer-shell.
        # create() força a criação do QWaylandWindow antes de Window::get().
        widget.winId()
        qwindow = widget.windowHandle()
        if qwindow is None:
            print("[layershell] windowHandle() é None após winId()")
            return None

        # setScreen ANTES de create(): winId() criou QWindow mas ainda não a
        # QWaylandWindow (platform window). setScreen() aqui apenas seta o campo
        # de tela sem disparar recriação. Após create(), o compositor associa a
        # superfície layer-shell ao output correto.
        if screen is not None:
            qwindow.setScreen(screen)
            print(f"[layershell] setScreen({screen.name()}) antes de create()")

        qwindow.create()  # força QWaylandWindow antes de Window::get()

        qwin_ptr = sip.unwrapinstance(qwindow)
        print(f"[layershell] QWindow ptr={hex(qwin_ptr)}")

        # static LayerShellQt::Window* Window::get(QWindow*)
        fn = lib["_ZN12LayerShellQt6Window3getEP7QWindow"]
        fn.restype = ctypes.c_void_p
        fn.argtypes = [ctypes.c_void_p]
        lsw = fn(ctypes.c_void_p(qwin_ptr))
        if lsw is None:
            print("[layershell] Window::get() retornou null — plugin liblayer-shell.so carregado?")
            return None
        print(f"[layershell] Window::get() OK → lsw={hex(lsw)}")

        # void setLayer(Layer)
        fn = lib["_ZN12LayerShellQt6Window8setLayerENS0_5LayerE"]
        fn.restype = None
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
        fn(ctypes.c_void_p(lsw), ctypes.c_int(layer))
        print(f"[layershell] setLayer({layer}) OK")

        # void setKeyboardInteractivity(KeyboardInteractivity)
        fn = lib["_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE"]
        fn.restype = None
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
        fn(ctypes.c_void_p(lsw), ctypes.c_int(kbd))

        # void setAnchors(QFlags<Anchor>)
        fn = lib["_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE"]
        fn.restype = None
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
        fn(ctypes.c_void_p(lsw), ctypes.c_int(anchors))

        # void setExclusiveZone(int)
        fn = lib["_ZN12LayerShellQt6Window16setExclusiveZoneEi"]
        fn.restype = None
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
        fn(ctypes.c_void_p(lsw), ctypes.c_int(exclusive_zone))

        # void setMargins(const QMargins&) — posição via margens
        if initial_pos is not None:
            x, y = initial_pos
        else:
            p = widget.pos()
            x, y = p.x(), p.y()
        _set_margins(lib, lsw, x, y)
        print(f"[layershell] apply() concluído — layer={layer} pos=({x},{y}) excl={exclusive_zone}")

        # Aproveitar que a integração Wayland está activa para cachear wl_compositor/display.
        # Em PyQt6, platformNativeInterface() não está acessível como classmethod —
        # deve ser chamado na instância. Fazemos isso aqui onde tudo já está inicializado.
        _cache_wl_globals(widget)

        return lsw  # raw pointer (int) for later move_to() calls

    except Exception as e:
        print(f"[layershell] error: {e}")
        return None


def move_to(lsw_ptr: int, x: int, y: int) -> None:
    """Reposition an already-shown layer-shell window by updating its margins."""
    lib = _get_lib()
    if lib is None:
        return
    try:
        _set_margins(lib, lsw_ptr, x, y)
    except Exception:
        pass


def _set_margins(lib, lsw_ptr: int, x: int, y: int) -> None:
    fn = lib["_ZN12LayerShellQt6Window10setMarginsERK8QMargins"]
    fn.restype = None
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_QMargins)]
    m = _QMargins(m_left=x, m_top=y, m_right=0, m_bottom=0)
    fn(ctypes.c_void_p(lsw_ptr), ctypes.byref(m))


# ── Wayland input-region direto ───────────────────────────────────────────────
# Qt mapeia setMask(QRegion()) → set_input_region(NULL) = aceita tudo (errado).
# A diferença protocolar:
#   NULL          → aceita input em toda a superfície
#   empty_region  → rejeita todo input (wl_region sem rectângulos)
# Só o caminho direto via libwayland-client garante o comportamento correto.

_wl_client      = None
_wl_compositor  = None   # wl_compositor* cacheado em apply()
_wl_display_ptr = None   # wl_display*    cacheado em apply()


def _get_wl_client():
    global _wl_client
    if _wl_client is not None:
        return _wl_client
    for name in ("libwayland-client.so.0", "libwayland-client.so"):
        try:
            _wl_client = ctypes.CDLL(name)
            return _wl_client
        except OSError:
            pass
    print("[layershell] libwayland-client não encontrada")
    return None


def _get_nif():
    """Devolve QPlatformNativeInterface ou None.

    PyQt6 expõe como método de instância — não funciona como classmethod.
    """
    try:
        from PyQt6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is None:
            return None
        # Instância first; fallback para classmethod (alguns builds antigos)
        for name in ("platformNativeInterface",):
            fn = getattr(app, name, None) or getattr(QGuiApplication, name, None)
            if callable(fn):
                try:
                    nif = fn()
                    if nif is not None:
                        return nif
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _load_qt_wayland_client():
    """Carrega libQt6WaylandClient.so.6 (ou variante) via ctypes."""
    for name in ("libQt6WaylandClient.so.6", "libQt6WaylandClient.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            pass
    return None


def _cache_wl_globals(widget) -> None:
    """Cacheia wl_compositor* e wl_display*.

    Caminho 1: QPlatformNativeInterface via instância QGuiApplication (builds que o expõem).
    Caminho 2: QNativeInterface.QWaylandApplication.display() (API pública PyQt6) +
               wl_registry roundtrip numa fila privada para obter wl_compositor* sem
               interferir com a fila principal do Qt.
               Necessário porque Qt6WaylandClient do pip tem símbolos internos com
               visibility=hidden (QWaylandIntegration::instance() não exportado).
    """
    global _wl_compositor, _wl_display_ptr
    if _wl_compositor is not None:
        return

    # ── Caminho 1: NIF ────────────────────────────────────────────────────────
    try:
        nif = _get_nif()
        if nif is not None:
            comp = (nif.nativeResourceForIntegration(b"compositor") or
                    nif.nativeResourceForIntegration(b"wl_compositor"))
            disp = (nif.nativeResourceForIntegration(b"wl_display") or
                    nif.nativeResourceForIntegration(b"display"))
            if comp:
                _wl_compositor  = comp
                _wl_display_ptr = disp
                print(f"[layershell] wl globals via NIF: compositor={hex(comp)}")
                return
    except Exception as e:
        print(f"[layershell] _cache_wl_globals NIF: {e}")

    # ── Caminho 2: QNativeInterface.QWaylandApplication + wl_registry ────────
    try:
        from PyQt6.QtCore import QNativeInterface
        from PyQt6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return

        wa = app.nativeInterface(QNativeInterface.QWaylandApplication)
        if wa is None:
            print("[layershell] QNativeInterface.QWaylandApplication indisponível")
            return

        disp_ptr = int(wa.display())
        if not disp_ptr:
            print("[layershell] display() NULL")
            return

        wl = _get_wl_client()
        if wl is None:
            return

        # Fila privada: roundtrip só despacha eventos desta fila, não toca na
        # fila principal do Qt.
        wl.wl_display_create_queue.restype  = ctypes.c_void_p
        wl.wl_display_create_queue.argtypes = [ctypes.c_void_p]
        queue = wl.wl_display_create_queue(ctypes.c_void_p(disp_ptr))
        if not queue:
            print("[layershell] wl_display_create_queue NULL")
            return

        wl.wl_display_get_registry.restype  = ctypes.c_void_p
        wl.wl_display_get_registry.argtypes = [ctypes.c_void_p]
        registry = wl.wl_display_get_registry(ctypes.c_void_p(disp_ptr))
        if not registry:
            print("[layershell] wl_display_get_registry NULL")
            wl.wl_event_queue_destroy.restype  = None
            wl.wl_event_queue_destroy.argtypes = [ctypes.c_void_p]
            wl.wl_event_queue_destroy(ctypes.c_void_p(queue))
            return

        wl.wl_proxy_set_queue.restype  = None
        wl.wl_proxy_set_queue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        wl.wl_proxy_set_queue(ctypes.c_void_p(registry), ctypes.c_void_p(queue))

        compositor_result = [None]

        GLOBAL_CB = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,   # data
            ctypes.c_void_p,   # registry
            ctypes.c_uint32,   # name (global id)
            ctypes.c_char_p,   # interface name
            ctypes.c_uint32,   # version
        )
        GLOBAL_REMOVE_CB = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,   # data
            ctypes.c_void_p,   # registry
            ctypes.c_uint32,   # name
        )

        @GLOBAL_CB
        def _on_global(data, reg, name, iface, ver):
            if iface == b"wl_compositor" and compositor_result[0] is None:
                try:
                    # wl_compositor_interface é símbolo público em libwayland-client
                    class _WlIface(ctypes.Structure):
                        _fields_ = [("_", ctypes.c_byte)]
                    iface_obj = _WlIface.in_dll(wl, "wl_compositor_interface")
                    wl.wl_registry_bind.restype  = ctypes.c_void_p
                    wl.wl_registry_bind.argtypes = [
                        ctypes.c_void_p, ctypes.c_uint32,
                        ctypes.c_void_p, ctypes.c_uint32,
                    ]
                    comp = wl.wl_registry_bind(
                        ctypes.c_void_p(reg), name,
                        ctypes.addressof(iface_obj), min(ver, 4),
                    )
                    if comp:
                        # Mover compositor para a fila principal (NULL = default queue)
                        wl.wl_proxy_set_queue(ctypes.c_void_p(comp), None)
                        compositor_result[0] = comp
                        print(f"[layershell] wl_compositor bound: name={name} ptr={hex(comp)}")
                except Exception as ex:
                    print(f"[layershell] registry bind erro: {ex}")

        @GLOBAL_REMOVE_CB
        def _on_global_remove(data, reg, name):
            pass

        class _RegistryListener(ctypes.Structure):
            _fields_ = [
                ("cb_global", ctypes.c_void_p),
                ("cb_remove", ctypes.c_void_p),
            ]

        listener = _RegistryListener(
            cb_global = ctypes.cast(_on_global,        ctypes.c_void_p).value,
            cb_remove = ctypes.cast(_on_global_remove, ctypes.c_void_p).value,
        )

        wl.wl_proxy_add_listener.restype  = ctypes.c_int
        wl.wl_proxy_add_listener.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        wl.wl_proxy_add_listener(
            ctypes.c_void_p(registry), ctypes.byref(listener), None
        )

        wl.wl_display_roundtrip_queue.restype  = ctypes.c_int
        wl.wl_display_roundtrip_queue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        wl.wl_display_roundtrip_queue(ctypes.c_void_p(disp_ptr), ctypes.c_void_p(queue))

        # Limpar registry e fila privada (compositor já está na fila principal)
        wl.wl_proxy_destroy.restype  = None
        wl.wl_proxy_destroy.argtypes = [ctypes.c_void_p]
        wl.wl_proxy_destroy(ctypes.c_void_p(registry))

        wl.wl_event_queue_destroy.restype  = None
        wl.wl_event_queue_destroy.argtypes = [ctypes.c_void_p]
        wl.wl_event_queue_destroy(ctypes.c_void_p(queue))

        if compositor_result[0]:
            _wl_compositor  = compositor_result[0]
            _wl_display_ptr = disp_ptr
            print(f"[layershell] wl globals via registry: "
                  f"compositor={hex(_wl_compositor)} display={hex(_wl_display_ptr)}")
        else:
            print("[layershell] wl_compositor não encontrado no registry")

    except Exception as e:
        print(f"[layershell] _cache_wl_globals registry erro: {e}")


def _get_wl_surface(widget) -> "int | None":
    """Devolve wl_surface* do widget.

    Caminho 1: NIF nativeResourceForWindow(b"surface", wh).
    Caminho 2: QWindow::handle() [símbolo público em libQt6Gui] → QPlatformWindow*
               → QWaylandWindow* (subtraindo 8, offset da base QPlatformWindow)
               → vtable[2] = surface() const  (índice 2 após os dois destrutores virtuais)
               → validado com wl_proxy_get_class(b"wl_surface").
               Necessário porque QWaylandWindow::surface() tem visibility=hidden no pip.
    """
    try:
        wh = widget.windowHandle()
        if wh is None:
            return None

        # Caminho 1: NIF
        nif = _get_nif()
        if nif is not None:
            sf = nif.nativeResourceForWindow(b"surface", wh)
            if sf:
                return sf

        # Caminho 2: QWindow::handle() + vtable de QWaylandWindow
        import PyQt6.sip as sip
        qwin_ptr = sip.unwrapinstance(wh)
        if not qwin_ptr:
            return None

        qtgui = None
        for name in ("libQt6Gui.so.6", "libQt6Gui.so"):
            try:
                qtgui = ctypes.CDLL(name)
                break
            except OSError:
                pass
        if qtgui is None:
            print("[layershell] _get_wl_surface: libQt6Gui não encontrada")
            return None

        # QPlatformWindow* QWindow::handle() const  [exportado em libQt6Gui]
        handle_fn = qtgui["_ZNK7QWindow6handleEv"]
        handle_fn.restype  = ctypes.c_void_p
        handle_fn.argtypes = [ctypes.c_void_p]
        platform_win = handle_fn(ctypes.c_void_p(qwin_ptr))
        if not platform_win:
            print("[layershell] _get_wl_surface: QWindow::handle() NULL")
            return None

        wl = _get_wl_client()
        if wl is None:
            return None

        wl.wl_proxy_get_class.restype  = ctypes.c_char_p
        wl.wl_proxy_get_class.argtypes = [ctypes.c_void_p]

        # Layout de QWaylandWindow:
        #   offset 0: vptr → vtable de QNativeInterface::Private::QWaylandWindow
        #             vtable[0]=D1 destrutor, vtable[1]=D0 destrutor, vtable[2]=surface()
        #   offset 8: QPlatformWindow (segunda base)
        # handle() devolve QPlatformWindow* = QWaylandWindow* + 8 → subtrai 8.
        SURFACE_FN = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)

        for obj_offset in (8, 0, 16):
            qwayland_win = platform_win - obj_offset
            if qwayland_win <= 0:
                continue
            try:
                # Lê vtable pointer (primeiros 8 bytes do objecto)
                vtable_ptr = ctypes.cast(
                    qwayland_win, ctypes.POINTER(ctypes.c_uint64)
                )[0]
                if not vtable_ptr:
                    continue
                # vtable[2] = surface() const  (offset 16 = 2 × 8 bytes)
                fn_ptr = ctypes.cast(
                    vtable_ptr + 16, ctypes.POINTER(ctypes.c_uint64)
                )[0]
                if not fn_ptr:
                    continue
                sf = SURFACE_FN(fn_ptr)(ctypes.c_void_p(qwayland_win))
                if sf and sf > 0x1000:
                    cls = wl.wl_proxy_get_class(ctypes.c_void_p(sf))
                    if cls == b"wl_surface":
                        print(f"[layershell] wl_surface via vtable obj_off={obj_offset}: {hex(sf)}")
                        return sf
            except Exception:
                pass

        print("[layershell] _get_wl_surface: vtable não retornou wl_surface válido")
        return None
    except Exception as e:
        print(f"[layershell] _get_wl_surface erro: {e}")
        return None


def _wl_flush(wl, display_ptr) -> None:
    if not display_ptr:
        return
    try:
        wl.wl_display_flush.restype  = ctypes.c_int
        wl.wl_display_flush.argtypes = [ctypes.c_void_p]
        wl.wl_display_flush(ctypes.c_void_p(display_ptr))
    except Exception:
        pass


def set_empty_input_region(widget) -> bool:
    """Define a input region do wl_surface como vazia → rejeita todo input.

    Wayland: set_input_region(empty_region) ≠ set_input_region(NULL).
    NULL = aceita tudo; empty_region (0 rectângulos) = rejeita tudo.
    Retorna True se bem-sucedido.
    """
    if not IS_WAYLAND:
        return False
    wl = _get_wl_client()
    if wl is None:
        return False
    if _wl_compositor is None:
        _cache_wl_globals(widget)
    surface    = _get_wl_surface(widget)
    compositor = _wl_compositor
    display    = _wl_display_ptr
    if not surface or not compositor:
        print(f"[layershell] set_empty_input_region: surface={surface!r} "
              f"compositor={compositor!r} — passthrough indisponível")
        return False
    try:
        wl.wl_compositor_create_region.restype  = ctypes.c_void_p
        wl.wl_compositor_create_region.argtypes = [ctypes.c_void_p]
        wl.wl_surface_set_input_region.restype  = None
        wl.wl_surface_set_input_region.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        wl.wl_surface_commit.restype            = None
        wl.wl_surface_commit.argtypes           = [ctypes.c_void_p]
        wl.wl_region_destroy.restype            = None
        wl.wl_region_destroy.argtypes           = [ctypes.c_void_p]

        empty_region = wl.wl_compositor_create_region(ctypes.c_void_p(compositor))
        if not empty_region:
            print("[layershell] set_empty_input_region: wl_compositor_create_region → NULL")
            return False
        # Sem wl_region_add → região com zero rectângulos → rejeita tudo
        wl.wl_surface_set_input_region(ctypes.c_void_p(surface),
                                       ctypes.c_void_p(empty_region))
        wl.wl_surface_commit(ctypes.c_void_p(surface))
        wl.wl_region_destroy(ctypes.c_void_p(empty_region))
        _wl_flush(wl, display)
        print("[layershell] set_empty_input_region OK")
        return True
    except Exception as e:
        print(f"[layershell] set_empty_input_region erro: {e}")
        return False


def clear_input_region(widget) -> bool:
    """Restaura a input region do wl_surface para NULL → aceita todo input.

    Retorna True se bem-sucedido.
    """
    if not IS_WAYLAND:
        return False
    wl = _get_wl_client()
    if wl is None:
        return False
    surface = _get_wl_surface(widget)
    display = _wl_display_ptr
    if not surface:
        return False
    try:
        wl.wl_surface_set_input_region.restype  = None
        wl.wl_surface_set_input_region.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        wl.wl_surface_commit.restype            = None
        wl.wl_surface_commit.argtypes           = [ctypes.c_void_p]
        # NULL = aceita todo input
        wl.wl_surface_set_input_region(ctypes.c_void_p(surface), None)
        wl.wl_surface_commit(ctypes.c_void_p(surface))
        _wl_flush(wl, display)
        print("[layershell] clear_input_region OK")
        return True
    except Exception as e:
        print(f"[layershell] clear_input_region erro: {e}")
        return False

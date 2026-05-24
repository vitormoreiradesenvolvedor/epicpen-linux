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

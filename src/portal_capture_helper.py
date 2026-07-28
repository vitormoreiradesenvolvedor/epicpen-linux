"""Captura via xdg-desktop-portal ScreenCast + GStreamer (cross-desktop).

Ao contrário do capture_helper.py (QScreenCapture, só monitor inteiro e
sempre com o cursor embutido), este helper usa o portal freedesktop padrão
`org.freedesktop.portal.ScreenCast` — o MESMO que OBS/Discord — e por isso
funciona em KDE, GNOME, Cinnamon, wlroots etc. e dá controle de:

  • fonte: um MONITOR ou uma JANELA específica (o portal mostra o seletor);
  • cursor: embutido (mostrar) ou oculto na gravação.

O stream PipeWire negociado pelo portal é consumido por GStreamer
(pipewiresrc → appsink) e os frames crus saem no stdout no MESMO protocolo
do capture_helper.py, para o recorder.py bombear igual:

  linha 1 : JSON {"w","h","stride","pix_fmt"[, "restore_token"]} + "\n"
  depois  : frames de exatamente stride*h bytes cada.

Deduplicação (encode-on-change) idêntica ao capture_helper: frame igual ao
último não desce o pipe; reenvio a cada 1s limita o corte de cauda.

Uso:
    python portal_capture_helper.py '<json-opts>'
    python portal_capture_helper.py --selftest      # videotestsrc, sem portal

json-opts: {"source":"monitor"|"window","cursor":bool,"restore_token":str|null}
"""
import json
import os
import queue
import signal
import sys
import threading
import time

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstApp, GstVideo, GLib, Gio  # noqa: E402

_RESEND_INTERVAL = 1.0

# Tipos de fonte e modos de cursor do portal ScreenCast (freedesktop spec).
_TYPE_MONITOR = 1
_TYPE_WINDOW = 2
_CURSOR_HIDDEN = 1
_CURSOR_EMBEDDED = 2
# persist_mode: 0 = não persistir (seletor toda vez); 2 = lembrar a fonte
# (auto-restaura com o restore_token, pula o seletor). Janela usa 0 (escolher
# janelas diferentes); monitor/região usam 2 (mesma tela, sem repetir seletor).
_PERSIST_NONE = 0
_PERSIST_UNTIL_REVOKED = 2

_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_OBJ = "/org/freedesktop/portal/desktop"
_SC_IFACE = "org.freedesktop.portal.ScreenCast"
_REQ_IFACE = "org.freedesktop.portal.Request"


def should_send(data: bytes, last_data, now: float, last_sent: float,
                resend: float = _RESEND_INTERVAL) -> bool:
    """True se o frame deve descer o pipe: mudou, ou é hora do reenvio
    periódico (bytes == bytes é memcmp em C com early-exit)."""
    if data != last_data:
        return True
    return (now - last_sent) >= resend


# ── Handshake do portal ─────────────────────────────────────────────────────

class PortalError(Exception):
    pass


def _new_token(prefix: str) -> str:
    return f"epicpen_{prefix}_{os.getpid()}_{int(time.monotonic() * 1000) & 0xffffff}"


def _request_path(conn: Gio.DBusConnection, token: str) -> str:
    """Caminho previsível do objeto Request para este token (spec do portal)."""
    sender = conn.get_unique_name()[1:].replace(".", "_")
    return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"


def _call_request(conn: Gio.DBusConnection, method: str,
                  build_params) -> dict:
    """Chama um método baseado em Request e espera o sinal Response.

    build_params(handle_token) → GLib.Variant com os args do método (o
    handle_token entra nas options). Assina o Response ANTES da chamada
    (caminho previsível) para nunca perder o sinal. Retorna results (a{sv}
    já desempacotado) ou levanta PortalError se cancelado/encerrado.
    """
    token = _new_token(method.lower())
    req_path = _request_path(conn, token)
    loop = GLib.MainLoop()
    box = {"response": None, "results": None}

    def _on_response(_c, _sender, _path, _iface, _signal, params):
        resp, results = params.unpack()
        box["response"] = resp
        box["results"] = results
        loop.quit()

    sub = conn.signal_subscribe(
        _PORTAL_BUS, _REQ_IFACE, "Response", req_path, None,
        Gio.DBusSignalFlags.NO_MATCH_RULE, _on_response,
    )
    try:
        conn.call_sync(
            _PORTAL_BUS, _PORTAL_OBJ, _SC_IFACE, method,
            build_params(token), GLib.VariantType.new("(o)"),
            Gio.DBusCallFlags.NONE, -1, None,
        )
        # Timeout de segurança: sem resposta em 5min, aborta (seletor fechado
        # sem escolha em compositores que não emitem Response de cancelamento).
        GLib.timeout_add_seconds(300, lambda: (loop.quit(), False)[1])
        loop.run()
    finally:
        conn.signal_unsubscribe(sub)

    if box["response"] is None:
        raise PortalError(f"{method}: sem resposta do portal")
    if box["response"] != 0:
        raise PortalError(f"{method}: cancelado (response={box['response']})")
    return box["results"] or {}


def portal_open_stream(source: str, show_cursor: bool,
                       restore_token: str | None, persist: bool = False):
    """Roda o handshake do portal e devolve (fd_pipewire, node_id, token, keep).

    O portal mostra o seletor (uma vez; com restore_token pode nem mostrar).
    Levanta PortalError em falha/cancelamento.

    `keep` = (conn, out_fds) — objetos que o CHAMADOR PRECISA manter vivos
    durante toda a captura. Se a conexão DBus for coletada pelo GC, o portal
    fecha a sessão ScreenCast e o node PipeWire morre; o pipewiresrc então
    reconecta ao daemon padrão e captura a PRIMEIRA fonte de vídeo — a webcam.
    Manter out_fds vivo evita que o GUnixFDList feche o fd ao ser finalizado.
    """
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    # 1. CreateSession
    def _create(tok):
        return GLib.Variant("(a{sv})", ({
            "handle_token": GLib.Variant("s", tok),
            "session_handle_token": GLib.Variant("s", _new_token("sess")),
        },))
    res = _call_request(conn, "CreateSession", _create)
    session = res.get("session_handle")
    if not session:
        raise PortalError("CreateSession não retornou session_handle")

    # 2. SelectSources — fonte e cursor
    types = _TYPE_WINDOW if source == "window" else _TYPE_MONITOR
    cursor = _CURSOR_EMBEDDED if show_cursor else _CURSOR_HIDDEN

    def _select(tok):
        opts = {
            "handle_token": GLib.Variant("s", tok),
            "types": GLib.Variant("u", types),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", cursor),
            # persist só quando pedido (região) → restore_token pula o seletor
            # nas próximas. Monitor/janela: sem persist (seletor sempre).
            "persist_mode": GLib.Variant(
                "u", _PERSIST_UNTIL_REVOKED if persist else _PERSIST_NONE),
        }
        if restore_token:
            opts["restore_token"] = GLib.Variant("s", restore_token)
        return GLib.Variant("(oa{sv})", (session, opts))
    _call_request(conn, "SelectSources", _select)

    # 3. Start — mostra o seletor; devolve os streams e o restore_token
    def _start(tok):
        return GLib.Variant("(osa{sv})", (
            session, "", {"handle_token": GLib.Variant("s", tok)},
        ))
    res = _call_request(conn, "Start", _start)
    streams = res.get("streams") or []
    if not streams:
        raise PortalError("nenhuma fonte selecionada")
    node_id = int(streams[0][0])
    # Propriedades do stream: "position" (x,y do monitor no layout) identifica
    # QUAL monitor foi capturado — necessário quando há telas de mesma resolução
    # (tamanho não distingue). Nem todo backend fornece; None se ausente.
    props = streams[0][1] if len(streams[0]) > 1 else {}
    position = props.get("position")
    new_token = res.get("restore_token")

    # 4. OpenPipeWireRemote — fd do stream
    reply, out_fds = conn.call_with_unix_fd_list_sync(
        _PORTAL_BUS, _PORTAL_OBJ, _SC_IFACE, "OpenPipeWireRemote",
        GLib.Variant("(oa{sv})", (session, {})),
        GLib.VariantType.new("(h)"), Gio.DBusCallFlags.NONE, -1, None, None,
    )
    fd = out_fds.get(reply.unpack()[0])
    return fd, node_id, new_token, (conn, out_fds, session), position


def close_session(keep) -> None:
    """Fecha explicitamente a sessão ScreenCast (org.freedesktop.portal.Session
    .Close) → o KDE remove o ícone vermelho de transmissão na hora. Sem isto o
    ícone às vezes persiste (e acumula a cada gravação) esperando a conexão DBus
    cair. keep = (conn, out_fds, session)."""
    try:
        conn, _fds, session = keep
    except (TypeError, ValueError):
        return
    if not session:
        return
    try:
        conn.call_sync(
            _PORTAL_BUS, session, "org.freedesktop.portal.Session",
            "Close", None, None, Gio.DBusCallFlags.NONE, -1, None,
        )
    except Exception:
        pass


# ── Consumo do stream + protocolo de stdout ─────────────────────────────────

def _build_pipeline(source_desc: str):
    """Pipeline GStreamer: <source> → BGRA → appsink em modo pull.

    As caps de saída (BGRA) são fixadas no próprio appsink, não num capsfilter
    inline — evita depender do plugin coreelements no bundle do AppImage.

    A saída é travada no tamanho do 1º frame (ver run()) porque o ffmpeg roda
    com -video_size fixo. O videoscale garante que qualquer frame de tamanho
    diferente vira o tamanho travado (mantém o stream consistente). NÃO tentamos
    acompanhar o resize da janela durante a gravação: no KDE o buffer do
    ScreenCast fica no tamanho inicial e, ao encolher a janela, o compositor a
    ancora num canto e preenche o resto com preto NA PRÓPRIA fonte — não há o
    que escalar. Portanto: dimensione a janela ANTES de gravar.
    """
    Gst.init(None)
    desc = (
        f"{source_desc} ! videoconvert ! videoscale ! "
        "appsink name=sink emit-signals=false max-buffers=6 drop=true sync=false"
    )
    pipeline = Gst.parse_launch(desc)
    appsink = pipeline.get_by_name("sink")
    appsink.set_property("caps", Gst.Caps.from_string("video/x-raw,format=BGRA"))
    return pipeline, appsink


def _caps_geometry(caps) -> tuple[int, int, int, str]:
    """(w, h, stride, pix_fmt ffmpeg) a partir das caps negociadas."""
    vinfo = GstVideo.VideoInfo.new_from_caps(caps)
    w, h = vinfo.width, vinfo.height
    try:
        stride = vinfo.stride[0]
    except (TypeError, IndexError):
        stride = w * 4
    if stride <= 0:
        stride = w * 4
    return w, h, stride, "bgra"


def run(pipeline, appsink, restore_token: str | None, stream_pos=None) -> int:
    """Bombeia frames do appsink para o stdout até EOS/erro/sinal."""
    out = sys.stdout.buffer
    q: queue.Queue = queue.Queue(maxsize=8)
    state = {"hdr": False, "nbytes": 0, "last": None, "last_ts": 0.0,
             "running": True}

    def _writer():
        while True:
            item = q.get()
            if item is None:
                break
            try:
                out.write(item)
                out.flush()
            except (BrokenPipeError, OSError, ValueError):
                state["running"] = False
                break

    writer = threading.Thread(target=_writer, daemon=True,
                              name="epicpen-portal-writer")
    writer.start()

    def _puller():
        while state["running"]:
            try:
                sample = appsink.try_pull_sample(200 * Gst.MSECOND)
            except Exception:
                break
            if sample is None:
                if appsink.get_property("eos"):
                    break
                continue
            buf = sample.get_buffer()
            if not state["hdr"]:
                w, h, _stride, fmt = _caps_geometry(sample.get_caps())
                if w <= 0 or h <= 0:
                    continue
                # TRAVA o tamanho de saída no 1º frame: o videoscale passa a
                # escalar qualquer frame de tamanho diferente para w×h, mantendo
                # o -video_size fixo do ffmpeg. Com width fixo o stride vira w*4
                # (BGRA 4-alinhado) — descartamos este 1º frame (stride/tamanho
                # pré-trava) e usamos os próximos.
                appsink.set_property("caps", Gst.Caps.from_string(
                    f"video/x-raw,format=BGRA,width={w},height={h}"))
                stride = w * 4
                state["nbytes"] = stride * h
                header = {"w": w, "h": h, "stride": stride, "pix_fmt": fmt}
                if restore_token:
                    header["restore_token"] = restore_token
                if stream_pos:
                    try:
                        header["pos_x"] = int(stream_pos[0])
                        header["pos_y"] = int(stream_pos[1])
                    except (TypeError, ValueError, IndexError):
                        pass
                q.put((json.dumps(header) + "\n").encode())
                state["hdr"] = True
                continue   # descarta o 1º frame (tamanho/stride pré-trava)

            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if not ok:
                continue
            try:
                n = state["nbytes"]
                data = bytes(mapinfo.data[:n])
            finally:
                buf.unmap(mapinfo)
            if len(data) < state["nbytes"]:
                continue  # frame anômalo — descarta p/ não dessincronizar
            now = time.monotonic()
            if not should_send(data, state["last"], now, state["last_ts"]):
                continue
            state["last"] = data
            state["last_ts"] = now
            try:
                q.put_nowait(data)
            except queue.Full:
                pass  # consumidor não acompanha — descarta
        q.put(None)

    puller = threading.Thread(target=_puller, daemon=True,
                              name="epicpen-portal-puller")

    loop = GLib.MainLoop()

    def _stop(*_):
        state["running"] = False
        loop.quit()
        return False

    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, _stop)

    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def _on_bus(_bus, msg):
        if msg.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
            _stop()
    bus.connect("message", _on_bus)

    pipeline.set_state(Gst.State.PLAYING)
    puller.start()
    loop.run()

    state["running"] = False
    pipeline.set_state(Gst.State.NULL)
    puller.join(timeout=3)
    q.put(None)
    writer.join(timeout=3)
    try:
        out.flush()
    except Exception:
        pass
    # 0 = pegou frame(s) (normal); 2 = terminou SEM nenhum frame → o pipeline
    # falhou antes de começar (ex: pw_loop_new/support.system transitório) e o
    # chamador pode reconstruir e tentar de novo com o mesmo fd/node.
    return 0 if state["hdr"] else 2


def _check() -> int:
    """Sonda se o ambiente consegue consumir o stream: gi + Gst + pipewiresrc.
    Usado por portalcast.available() no AppImage (env do bundle isolado)."""
    try:
        Gst.init(None)
        return 0 if Gst.ElementFactory.find("pipewiresrc") is not None else 1
    except Exception:
        return 1


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--check":
        return _check()
    if args and args[0] == "--selftest":
        # Valida o caminho appsink→header→stdout sem portal nem interação.
        pipeline, appsink = _build_pipeline(
            "videotestsrc is-live=true num-buffers=15"
        )
        return run(pipeline, appsink, None)

    try:
        opts = json.loads(args[0]) if args else {}
    except (ValueError, IndexError):
        opts = {}
    source = opts.get("source", "monitor")
    show_cursor = bool(opts.get("cursor", True))
    restore_token = opts.get("restore_token") or None
    persist = bool(opts.get("persist", False))

    try:
        # keep (conn, out_fds) VIVO até o fim da captura — ver portal_open_stream.
        fd, node_id, new_token, keep, position = portal_open_stream(
            source, show_cursor, restore_token, persist)
    except PortalError as e:
        sys.stderr.write(json.dumps({"error": str(e)}) + "\n")
        sys.stderr.flush()
        return 2
    except Exception as e:  # noqa: BLE001 — reporta qualquer falha do portal
        sys.stderr.write(json.dumps({"error": f"portal: {e}"}) + "\n")
        sys.stderr.flush()
        return 2

    desc = (f"pipewiresrc fd={fd} path={node_id} do-timestamp=true "
            "keepalive-time=1000")
    try:
        # Retry: falhas transitórias do PipeWire (pw_loop_new/support.system)
        # às vezes impedem o 1º frame. Reconstrói o pipeline com o MESMO fd/node
        # (sem re-mostrar o seletor do portal) até 3x.
        rc = 2
        for attempt in range(3):
            pipeline, appsink = _build_pipeline(desc)
            rc = run(pipeline, appsink, new_token, position)
            if rc == 0:
                break
            sys.stderr.write(json.dumps(
                {"error": f"pipeline sem frames (tentativa {attempt + 1}/3)"}) + "\n")
            sys.stderr.flush()
            time.sleep(0.4)
    finally:
        # Fecha a sessão explicitamente → ícone vermelho do KDE some na hora.
        close_session(keep)
    return rc


if __name__ == "__main__":
    sys.exit(main())

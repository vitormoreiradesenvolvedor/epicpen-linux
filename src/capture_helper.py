"""Processo auxiliar de captura de tela (QScreenCapture → stdout).

Por que um processo separado: o Qt Multimedia NUNCA fecha a sessão de
ScreenCast do portal enquanto o processo vive — deleteLater e sip.delete
não encerram o stream PipeWire (medido com pw-dump: streams acumulam a
cada gravação e o KDE empilha ícones de transmissão). Com a captura num
processo próprio, o fim da gravação termina o processo, a conexão DBus
cai e o portal fecha a sessão na hora.

Protocolo (stdout, binário):
  1ª linha: JSON {"w","h","stride","pix_fmt"} + "\n"
  depois:   frames crus de exatamente stride*h bytes cada

A deduplicação (encode-on-change) acontece aqui: frame idêntico ao último
enviado não desce o pipe; reenvio a cada 1s limita o corte de cauda.
SIGTERM/SIGINT encerram graciosamente.

Uso: python capture_helper.py [nome_da_tela]
"""
import json
import queue
import signal
import sys
import threading
import time

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QMetaObject, Qt
from PyQt6.QtMultimedia import (
    QMediaCaptureSession, QScreenCapture, QVideoSink, QVideoFrame,
)

from recorder import (
    _frame_to_bytes, _get_native_fmts,
    _mem_available_bytes, _queue_frames,
)

_RESEND_INTERVAL = 1.0


def should_send(data: bytes, last_data, now: float, last_sent: float,
                resend: float = _RESEND_INTERVAL) -> bool:
    """True se o frame deve descer o pipe: mudou, ou é hora do reenvio
    periódico (bytes == bytes é memcmp em C com early-exit)."""
    if data != last_data:
        return True
    return (now - last_sent) >= resend


def main() -> int:
    screen_name = sys.argv[1] if len(sys.argv) > 1 else ""
    app = QApplication(sys.argv[:1])

    screens = app.screens()
    if not screens:
        return 1
    screen = next((s for s in screens if s.name() == screen_name), None)
    if screen is None:
        screen = max(screens, key=lambda s: s.refreshRate())

    capture = QScreenCapture()
    sink = QVideoSink()
    session = QMediaCaptureSession()
    session.setScreenCapture(capture)
    session.setVideoSink(sink)
    capture.setScreen(screen)

    def _request_quit():
        """Encerra o loop Qt de forma thread-safe.

        app.quit() chamado direto de uma thread ≠ main corrompe o event loop e
        aborta o processo (SIGBUS/SIGSEGV medido em coredump). Quando o helper
        morre assim em vez de sair limpo, a sessão ScreenCast/PipeWire cai
        anormalmente e o KDE avisa "câmera desconectada". invokeMethod com
        QueuedConnection posta o quit na thread do event loop."""
        QMetaObject.invokeMethod(app, "quit", Qt.ConnectionType.QueuedConnection)

    out = sys.stdout.buffer
    # Fila dimensionada pela RAM disponível (estratégia RAM): amortece
    # picos do consumidor sem travar a thread de captura
    est_frame = max(1, screen.size().width() * screen.size().height() * 4)
    q: queue.Queue = queue.Queue(
        maxsize=_queue_frames(est_frame, _mem_available_bytes())
    )

    state = {
        "hdr": False, "fmt": "rgba", "nbytes": 0,
        "last": None, "last_ts": 0.0,
    }

    def _writer():
        while True:
            item = q.get()
            if item is None:
                break
            try:
                out.write(item)
            except (BrokenPipeError, OSError, ValueError):
                _request_quit()   # thread-safe: NÃO chamar app.quit() daqui
                break

    writer = threading.Thread(target=_writer, daemon=True,
                              name="epicpen-capture-writer")
    writer.start()

    def _on_frame(frame: QVideoFrame):
        if not frame.isValid():
            return
        if not state["hdr"]:
            size = frame.size()
            w, h = size.width(), size.height()
            stride = 0
            if frame.map(QVideoFrame.MapMode.ReadOnly):
                try:
                    stride = frame.bytesPerLine(0)
                finally:
                    frame.unmap()
            if w <= 0 or h <= 0:
                return
            if stride <= 0:
                stride = w * 4
            state["fmt"] = _get_native_fmts().get(frame.pixelFormat(), "rgba")
            state["nbytes"] = stride * h
            header = json.dumps({
                "w": w, "h": h, "stride": stride, "pix_fmt": state["fmt"],
            }) + "\n"
            q.put(header.encode())
            state["hdr"] = True

        data = _frame_to_bytes(frame, state["fmt"])
        if data is None:
            return
        n = state["nbytes"]
        if len(data) > n:
            data = data[:n]
        elif len(data) < n:
            return  # frame anômalo — descartado para não dessincronizar
        now = time.monotonic()
        if not should_send(data, state["last"], now, state["last_ts"]):
            return
        state["last"] = data
        state["last_ts"] = now
        try:
            q.put_nowait(data)
        except queue.Full:
            pass  # consumidor não acompanha — descarta

    sink.videoFrameChanged.connect(_on_frame)

    # Stream ScreenCast pode cair sozinho (renegociação de formato ao cruzar
    # monitores de resolução/refresh diferentes, revogação de permissão do
    # portal). Sem este handler o helper ficava vivo entregando zero frames e
    # o KDE mantinha a "câmera" pendurada. errorOccurred → encerra limpo: o
    # EOF no pipe faz o recorder reportar a falha em vez de travar mudo.
    def _on_error(*_):
        _request_quit()
    try:
        capture.errorOccurred.connect(_on_error)
    except (AttributeError, TypeError):
        pass  # binding sem o signal (Qt antigo) — segue sem o handler

    capture.start()

    # Handlers Python só rodam entre bytecodes — o timer ocioso garante
    # que o loop Qt devolva o controle periodicamente
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(200)

    app.exec()

    capture.stop()
    q.put(None)
    writer.join(timeout=3)
    try:
        out.flush()
        out.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

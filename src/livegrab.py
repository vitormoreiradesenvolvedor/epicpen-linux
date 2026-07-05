"""Fonte de frames ao vivo para a lupa no Wayland.

Reusa o capture_helper.py do gravador: uma sessão de ScreenCast (portal)
por processo auxiliar, que fecha de verdade quando o processo morre —
deleteLater/sip.delete não encerram o stream PipeWire (ver capture_helper).

Uma thread leitora guarda apenas o frame mais recente; grab() recorta a
região pedida em coordenadas globais lógicas. Funciona em qualquer
compositor Wayland com portal de ScreenCast (KDE, GNOME, wlroots) — sem
depender de ferramentas externas como o grim.
"""
import json
import subprocess
import threading

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage, QPixmap

from recorder import _helper_cmd

# pix_fmt (nome ffmpeg, do header do helper) → formato QImage equivalente.
# bgra little-endian tem o mesmo layout de bytes que ARGB32.
_QIMAGE_FMT = {
    "bgra": QImage.Format.Format_ARGB32,
    "rgba": QImage.Format.Format_RGBA8888,
    "rgb0": QImage.Format.Format_RGBX8888,
}


class LiveFrameSource:
    """Mantém o último frame de um monitor via processo auxiliar."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest: bytes | None = None
        self._meta: tuple[int, int, int, QImage.Format] | None = None
        self._screen = None

    # ── Ciclo de vida ─────────────────────────────────────────────────────

    def start(self, screen) -> bool:
        """Sobe o helper para `screen` (QScreen). True se o processo subiu."""
        self.stop()
        try:
            self._proc = subprocess.Popen(
                _helper_cmd(screen.name()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._proc = None
            return False
        self._screen = screen
        self._thread = threading.Thread(
            target=self._reader, args=(self._proc,), daemon=True,
            name="epicpen-livegrab",
        )
        self._thread.start()
        return True

    def stop(self):
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except OSError:
                    pass
        self._thread = None
        self._screen = None
        with self._lock:
            self._latest = None
            self._meta = None

    @property
    def screen(self):
        return self._screen

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Leitura ───────────────────────────────────────────────────────────

    def _reader(self, proc: subprocess.Popen):
        """Thread: consome header + frames do helper, guardando só o último.

        read() de pipe libera a GIL — custo por frame na UI é zero.
        """
        try:
            header = json.loads(proc.stdout.readline())
            w = int(header["w"])
            h = int(header["h"])
            stride = int(header["stride"])
            fmt = _QIMAGE_FMT.get(str(header["pix_fmt"]),
                                  QImage.Format.Format_RGBA8888)
        except Exception:
            return  # helper morreu antes do 1º frame (portal negado, etc.)

        nbytes = stride * h
        read = proc.stdout.read
        while proc is self._proc:
            data = read(nbytes)
            if not data or len(data) < nbytes:
                break  # EOF — helper terminou
            with self._lock:
                self._latest = data
                self._meta = (w, h, stride, fmt)

    # ── Recorte ───────────────────────────────────────────────────────────

    def grab(self, x: int, y: int, w: int, h: int) -> QPixmap | None:
        """Recorta a região (coords globais lógicas) do frame mais recente."""
        with self._lock:
            data, meta = self._latest, self._meta
        if data is None or meta is None or self._screen is None:
            return None
        fw, fh, stride, fmt = meta

        geo: QRect = self._screen.geometry()
        if geo.width() <= 0 or geo.height() <= 0:
            return None
        # Frame vem em pixels físicos; coords da lupa são lógicas
        sx = fw / geo.width()
        sy = fh / geo.height()
        rect = QRect(
            round((x - geo.x()) * sx), round((y - geo.y()) * sy),
            max(1, round(w * sx)), max(1, round(h * sy)),
        ) & QRect(0, 0, fw, fh)
        if rect.isEmpty():
            return None

        # QImage sem cópia sobre `data` (mantido vivo pelo escopo); o
        # copy(rect) materializa apenas o recorte
        img = QImage(data, fw, fh, stride, fmt).copy(rect)
        return QPixmap.fromImage(img) if not img.isNull() else None

import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtMultimedia import QMediaCaptureSession, QScreenCapture, QVideoSink, QVideoFrame
from PyQt6.QtGui import QImage

_EXTRA_PATHS = [
    "/usr/bin", "/usr/local/bin", "/bin",
    str(Path.home() / ".local" / "bin"),
    "/snap/bin",
]

# Formatos nativos Qt → nome ffmpeg (single-plane; bits(0) = frame completo).
# Populado na primeira chamada para não importar enums antes do QApp existir.
_NATIVE_FMTS: Optional[dict] = None


def _get_native_fmts() -> dict:
    global _NATIVE_FMTS
    if _NATIVE_FMTS is None:
        from PyQt6.QtMultimedia import QVideoFrameFormat as F
        _NATIVE_FMTS = {
            F.PixelFormat.Format_BGRA8888: "bgra",
            F.PixelFormat.Format_BGRA8888_Premultiplied: "bgra",
            F.PixelFormat.Format_RGBA8888: "rgba",
            F.PixelFormat.Format_RGBX8888: "rgb0",
        }
    return _NATIVE_FMTS


def _native_pix_fmt(frame: QVideoFrame) -> str:
    """Retorna o pixel format ffmpeg do frame; 'rgba' como fallback seguro."""
    return _get_native_fmts().get(frame.pixelFormat(), "rgba")


def _map_frame_direct(frame: QVideoFrame) -> Optional[bytes]:
    """Copia bytes do frame via map() sem conversão de pixels. None se falhar."""
    if not frame.map(QVideoFrame.MapMode.ReadOnly):
        return None
    try:
        bits = frame.bits(0)
        n = frame.mappedBytes(0)
        bits.setsize(n)
        return bytes(bits)
    except Exception:
        return None
    finally:
        frame.unmap()


def _frame_to_bytes(frame: QVideoFrame, expected_fmt: str) -> Optional[bytes]:
    """Converte frame para bytes no formato esperado pelo ffmpeg.

    Caminho rápido: map() direto quando o formato nativo bate (zero conversão).
    Fallback: toImage() + convertToFormat(RGBA8888).
    """
    if _get_native_fmts().get(frame.pixelFormat()) == expected_fmt:
        data = _map_frame_direct(frame)
        if data is not None:
            return data

    image = frame.toImage()
    if image.isNull():
        return None
    if image.format() != QImage.Format.Format_RGBA8888:
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    return bytes(ptr)


def _which(tool: str) -> str | None:
    found = shutil.which(tool)
    if found:
        return found
    for p in _EXTRA_PATHS:
        candidate = os.path.join(p, tool)
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _save_dir() -> Path:
    for parent in (Path.home() / "Vídeos", Path.home() / "Videos"):
        if parent.exists():
            return parent / "EpicPen"
    return Path.home() / "Vídeos" / "EpicPen"


def _best_screen():
    """Retorna o monitor com maior taxa de atualização entre todos os conectados."""
    screens = QApplication.screens()
    if not screens:
        return QApplication.primaryScreen()
    return max(screens, key=lambda s: s.refreshRate())


def _find_ffmpeg() -> str | None:
    """Retorna caminho do ffmpeg: bundled no AppImage tem prioridade."""
    appdir = os.environ.get("APPDIR", "")
    if appdir:
        bundled = os.path.join(appdir, "usr", "bin", "ffmpeg")
        if os.access(bundled, os.X_OK):
            return bundled
    return _which("ffmpeg")


def _has_libx264(ffmpeg: str) -> bool:
    """Retorna True se este build do ffmpeg inclui o encoder libx264."""
    try:
        r = subprocess.run(
            [ffmpeg, "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return "libx264" in r.stdout
    except Exception:
        return False


def _build_ffmpeg_cmd(
    ffmpeg: str, w: int, h: int, fps: int, dest: str, has_x264: bool,
    pix_fmt: str = "rgba",
) -> list[str]:
    """Monta o comando ffmpeg para rawvideo via stdin → MP4 de saída."""
    base = [
        ffmpeg,
        "-use_wallclock_as_timestamps", "1",
        "-f", "rawvideo",
        "-pixel_format", pix_fmt,
        "-video_size", f"{w}x{h}",
        "-framerate", str(fps),
        "-i", "pipe:0",
    ]
    if has_x264:
        encode = [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "fastdecode",
            "-crf", "23",
            "-profile:v", "baseline",
            "-level", "4.0",
            "-x264opts",
            (
                "aq-mode=0:no-deblock:sliced-threads:threads=0:"
                "bframes=0:weightp=0:subme=0:trellis=0:rc-lookahead=0:sync-lookahead=0"
            ),
            "-pix_fmt", "yuv420p",
            "-g", "300",
            "-sc_threshold", "0",
        ]
    else:
        encode = ["-c:v", "mpeg4", "-q:v", "5"]

    return base + encode + ["-an", "-movflags", "+faststart", "-y", dest]


def _lower_priority():
    """preexec_fn: reduz niceness do ffmpeg para não roubar CPU do UI."""
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    except Exception:
        pass


class ScreenRecorder(QObject):
    started = pyqtSignal()
    stopped = pyqtSignal(str)   # path do arquivo salvo
    failed  = pyqtSignal(str)   # mensagem de erro

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture = QScreenCapture()
        self._sink = QVideoSink()
        self._session = QMediaCaptureSession()
        self._session.setScreenCapture(self._capture)
        self._session.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_frame)

        self._proc: Optional[subprocess.Popen] = None
        self._dest: Optional[Path] = None
        self._active = False
        # Fila pequena: 4 frames. Se o encoder atrasar, descarta em vez de acumular.
        self._frame_queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=4)
        self._writer_thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()

        # Configurados em start()
        self._ffmpeg_path: Optional[str] = None
        self._rec_w = 0
        self._rec_h = 0
        self._rec_fps = 0
        self._rec_has_x264 = False
        self._detected_fmt: Optional[str] = None
        self._last_frame_ts = 0.0
        self._min_frame_interval = 1.0 / 60

    @property
    def is_recording(self) -> bool:
        return self._active

    def start(self) -> bool:
        if self._active:
            return True

        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            self.failed.emit(
                "ffmpeg não encontrado.\n"
                "Instale com: sudo dnf install ffmpeg  (Fedora)\n"
                "             sudo apt install ffmpeg  (Ubuntu/Debian)"
            )
            return False

        screen = _best_screen()
        if screen is None:
            self.failed.emit("Nenhuma tela detectada.")
            return False

        geo = screen.geometry()
        self._rec_w = geo.width()
        self._rec_h = geo.height()
        # Captura no Hz nativo; limite de 60 fps mantém uso de CPU razoável
        self._rec_fps = max(1, min(int(round(screen.refreshRate())), 60))
        self._ffmpeg_path = ffmpeg
        self._rec_has_x264 = _has_libx264(ffmpeg)
        self._detected_fmt = None
        self._proc = None

        save_dir = _save_dir()
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._dest = save_dir / f"epicpen_rec_{ts}.mp4"

        self._min_frame_interval = 1.0 / self._rec_fps
        self._last_frame_ts = 0.0
        self._active = True

        self._capture.setScreen(screen)
        self._capture.start()
        self.started.emit()
        return True

    def _start_ffmpeg(self, pix_fmt: str) -> bool:
        """Inicia o processo ffmpeg. Chamado na primeira frame (thread multimedia)."""
        cmd = _build_ffmpeg_cmd(
            self._ffmpeg_path,
            self._rec_w, self._rec_h, self._rec_fps,
            str(self._dest),
            self._rec_has_x264,
            pix_fmt,
        )
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=_lower_priority,
            )
        except OSError as e:
            self.failed.emit(f"Falha ao iniciar ffmpeg: {e}")
            return False

        self._detected_fmt = pix_fmt
        self._writer_thread = threading.Thread(
            target=self._writer_loop, daemon=True, name="epicpen-recorder",
        )
        self._writer_thread.start()
        return True

    def stop(self):
        if not self._active:
            return
        self._active = False
        self._capture.stop()

        if self._writer_thread:
            self._frame_queue.put(None)
            self._writer_thread.join(timeout=5)
            self._writer_thread = None

        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

            dest = self._dest
            self._proc = None

            if dest and dest.exists() and dest.stat().st_size > 0:
                self.stopped.emit(str(dest))
            else:
                self.failed.emit("Gravação falhou ou arquivo vazio.")
        else:
            self.failed.emit("Nenhum frame capturado.")

    def _on_frame(self, frame: QVideoFrame):
        if not self._active:
            return

        # Throttle: descartar frames acima da taxa alvo
        now = time.monotonic()
        if now - self._last_frame_ts < self._min_frame_interval:
            return

        # Lazy start: detecta o formato nativo e inicia o FFmpeg na primeira frame
        if self._proc is None:
            with self._start_lock:
                if self._proc is None:
                    pix_fmt = _native_pix_fmt(frame)
                    if not self._start_ffmpeg(pix_fmt):
                        self._active = False
                        return

        self._last_frame_ts = now

        data = _frame_to_bytes(frame, self._detected_fmt)
        if data is None:
            return

        try:
            self._frame_queue.put_nowait(data)
        except queue.Full:
            pass  # frame descartado — encoder não acompanha

    def _writer_loop(self):
        """Thread dedicada: lê frames da fila e escreve no stdin do ffmpeg.
        Sem flush() — o pipe do SO gerencia o buffering.
        """
        while True:
            data = self._frame_queue.get()
            if data is None:
                break
            if self._proc is None or self._proc.poll() is not None:
                break
            try:
                self._proc.stdin.write(data)
            except (BrokenPipeError, OSError):
                break

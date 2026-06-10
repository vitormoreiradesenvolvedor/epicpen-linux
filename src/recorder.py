import os
import queue
import shutil
import subprocess
import threading
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
    ffmpeg: str, w: int, h: int, fps: int, dest: str, has_x264: bool
) -> list[str]:
    """Monta o comando ffmpeg para rawvideo via stdin → MP4 de saída."""
    base = [
        ffmpeg,
        # Wall-clock timestamps: cada frame recebe o horário real de chegada,
        # evitando vídeo acelerado causado por bursts iniciais do QVideoSink.
        "-use_wallclock_as_timestamps", "1",
        "-f", "rawvideo",
        "-pixel_format", "rgba",
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
                "aq-mode=0:no-deblock:sliced-threads:threads=2:"
                "bframes=0:weightp=0:subme=0:trellis=0:rc-lookahead=0:sync-lookahead=0"
            ),
            "-pix_fmt", "yuv420p",
            "-g", "300",
            "-sc_threshold", "0",
        ]
    else:
        # Fallback sem libx264: MPEG-4 nativo do FFmpeg (sem GPL)
        encode = ["-c:v", "mpeg4", "-q:v", "5"]

    return base + encode + ["-an", "-movflags", "+faststart", "-y", dest]


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
        self._frame_queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=16)
        self._writer_thread: Optional[threading.Thread] = None

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
        w, h = geo.width(), geo.height()
        fps = max(1, int(round(screen.refreshRate())))

        save_dir = _save_dir()
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._dest = save_dir / f"epicpen_rec_{ts}.mp4"

        has_x264 = _has_libx264(ffmpeg)
        cmd = _build_ffmpeg_cmd(ffmpeg, w, h, fps, str(self._dest), has_x264)

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            self.failed.emit(f"Falha ao iniciar ffmpeg: {e}")
            return False

        self._active = True
        self._writer_thread = threading.Thread(
            target=self._writer_loop, daemon=True, name="epicpen-recorder",
        )
        self._writer_thread.start()

        self._capture.setScreen(screen)
        self._capture.start()
        self.started.emit()
        return True

    def stop(self):
        if not self._active:
            return
        self._active = False
        self._capture.stop()

        self._frame_queue.put(None)  # sentinel para encerrar o writer
        if self._writer_thread:
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

    def _on_frame(self, frame: QVideoFrame):
        if not self._active:
            return
        image = frame.toImage()
        if image.isNull():
            return
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        ptr = image.bits()
        ptr.setsize(image.sizeInBytes())
        try:
            self._frame_queue.put_nowait(bytes(ptr))
        except queue.Full:
            pass  # frame descartado — encoder não acompanha a captura

    def _writer_loop(self):
        """Thread dedicada: lê frames da fila e escreve no stdin do ffmpeg."""
        while True:
            data = self._frame_queue.get()
            if data is None:
                break
            if self._proc is None or self._proc.poll() is not None:
                break
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                break

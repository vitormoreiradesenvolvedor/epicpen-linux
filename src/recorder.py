import glob
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


def _ffmpeg_candidates() -> list[str]:
    """Lista ordenada de binários ffmpeg disponíveis (bundled primeiro)."""
    out: list[str] = []
    appdir = os.environ.get("APPDIR", "")
    if appdir:
        bundled = os.path.join(appdir, "usr", "bin", "ffmpeg")
        if os.access(bundled, os.X_OK):
            out.append(bundled)
    system = _which("ffmpeg")
    if system and system not in out:
        out.append(system)
    return out


def _find_ffmpeg() -> str | None:
    """Retorna caminho do ffmpeg: bundled no AppImage tem prioridade."""
    cands = _ffmpeg_candidates()
    return cands[0] if cands else None


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


# Cache de probes VAAPI por binário ffmpeg — o teste real custa ~200ms,
# só vale a pena pagar uma vez por sessão.
_VAAPI_CACHE: dict[str, Optional[str]] = {}


def _probe_vaapi(ffmpeg: str) -> Optional[str]:
    """Retorna o device DRM com encode H.264 VAAPI comprovadamente funcional.

    Não confia na listagem de encoders: faz um encode real de teste por
    render node (driver presente ≠ driver funcional). None se nenhum servir.
    -init_hw_device é a sintaxe portátil (ffmpeg 4.x–8.x; -vaapi_device
    foi removido no 8).
    """
    if ffmpeg in _VAAPI_CACHE:
        return _VAAPI_CACHE[ffmpeg]
    found: Optional[str] = None
    for dev in sorted(glob.glob("/dev/dri/renderD*")):
        cmd = [
            ffmpeg, "-v", "error",
            "-init_hw_device", f"vaapi=va:{dev}",
            "-filter_hw_device", "va",
            "-f", "lavfi", "-i", "color=black:s=640x360:d=0.1:r=30",
            "-vf", "format=nv12,hwupload",
            "-c:v", "h264_vaapi",
            "-f", "null", "-",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=8)
            if r.returncode == 0:
                found = dev
                break
        except Exception:
            continue
    _VAAPI_CACHE[ffmpeg] = found
    return found


def _pick_ffmpeg() -> tuple[Optional[str], Optional[str], bool]:
    """Escolhe o melhor encoder disponível: (ffmpeg, vaapi_device, has_x264).

    Prioridade: VAAPI (encode na GPU, ~0% CPU) → libx264 ultrafast → mpeg4.
    O bundled do AppImage não tem VAAPI, por isso o ffmpeg do sistema também
    é considerado — quem oferecer GPU ganha.
    """
    cands = _ffmpeg_candidates()
    if not cands:
        return None, None, False
    for c in cands:
        dev = _probe_vaapi(c)
        if dev:
            return c, dev, _has_libx264(c)
    for c in cands:
        if _has_libx264(c):
            return c, None, True
    return cands[0], None, False


def _build_ffmpeg_cmd(
    ffmpeg: str, w: int, h: int, fps: int, dest: str, has_x264: bool,
    pix_fmt: str = "rgba",
    vaapi_device: Optional[str] = None,
) -> list[str]:
    """Monta o comando ffmpeg para rawvideo via stdin → MP4 de saída.

    -vsync vfr preserva os timestamps wallclock sem duplicar frames — é o que
    permite a deduplicação no _on_frame: tela estática gera zero trabalho de
    encode e o player simplesmente segura o último frame.
    """
    base = [ffmpeg]
    if vaapi_device:
        base += [
            "-init_hw_device", f"vaapi=va:{vaapi_device}",
            "-filter_hw_device", "va",
        ]
    base += [
        "-use_wallclock_as_timestamps", "1",
        "-f", "rawvideo",
        "-pixel_format", pix_fmt,
        "-video_size", f"{w}x{h}",
        "-framerate", str(fps),
        "-i", "pipe:0",
    ]
    if vaapi_device:
        # Encode 100% na GPU: hwupload + h264_vaapi. CPU só faz o memcpy do pipe.
        encode = [
            "-vf", "format=nv12,hwupload",
            "-c:v", "h264_vaapi",
            "-qp", "24",
            "-bf", "0",
            "-g", "300",
        ]
    elif has_x264:
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

    return base + encode + [
        "-vsync", "vfr",
        "-an", "-movflags", "+faststart", "-y", dest,
    ]


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
        self._rec_vaapi_dev: Optional[str] = None
        self._detected_fmt: Optional[str] = None
        self._last_frame_ts = 0.0
        self._min_frame_interval = 1.0 / 60

        # Deduplicação de frames (encode-on-change): frames idênticos ao último
        # enviado não vão para o encoder. bytes == bytes é memcmp em C com
        # early-exit — em tela estática o custo é ~1 leitura de memória e o
        # encoder fica 100% ocioso. Reenvio periódico limita o corte no fim.
        self._last_data: Optional[bytes] = None
        self._last_sent_ts = 0.0
        self._dup_resend_interval = 1.0

    @property
    def is_recording(self) -> bool:
        return self._active

    def start(self) -> bool:
        if self._active:
            return True

        ffmpeg, vaapi_dev, has_x264 = _pick_ffmpeg()
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
        self._rec_has_x264 = has_x264
        self._rec_vaapi_dev = vaapi_dev
        self._detected_fmt = None
        self._proc = None
        self._last_data = None
        self._last_sent_ts = 0.0

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
            vaapi_device=self._rec_vaapi_dev,
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
        self._last_data = None  # libera o frame retido pela deduplicação

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

        # Encode-on-change: frame idêntico ao anterior não vai para o encoder.
        if self._is_duplicate(data, now):
            return
        self._last_data = data
        self._last_sent_ts = now

        try:
            self._frame_queue.put_nowait(data)
        except queue.Full:
            pass  # frame descartado — encoder não acompanha

    def _is_duplicate(self, data: bytes, now: float) -> bool:
        """True se o frame é idêntico ao último enviado e ainda não é hora do
        reenvio periódico (que limita o corte de cauda do vídeo a ≤1s)."""
        if data != self._last_data:
            return False
        return (now - self._last_sent_ts) < self._dup_resend_interval

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

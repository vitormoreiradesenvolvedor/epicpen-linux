import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

from PyQt6.QtCore import QObject, QProcess, pyqtSignal
from PyQt6.QtWidgets import QApplication

_EXTRA_PATHS = [
    "/usr/bin", "/usr/local/bin", "/bin",
    str(Path.home() / ".local" / "bin"),
    "/snap/bin",
    "/usr/games",
    "/opt/bin",
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


def _detect_env() -> str:
    if (os.environ.get("WAYLAND_DISPLAY")
            and os.environ.get("QT_QPA_PLATFORM", "wayland") != "xcb"):
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if "gnome" in desktop:
            return "wayland-gnome"
        if "kde" in desktop or "plasma" in desktop:
            return "wayland-kde"
        return "wayland-wlroots"
    return "x11"


def _detect_vaapi() -> str | None:
    """Retorna /dev/dri/renderDXXX se VAAPI H.264 encoding disponível, senão None."""
    if not _which("vainfo"):
        return None
    for dev in ("/dev/dri/renderD128", "/dev/dri/renderD129"):
        if not os.path.exists(dev):
            continue
        try:
            r = subprocess.run(
                ["vainfo", "--display", "drm", "--device", dev],
                capture_output=True, text=True, timeout=5,
            )
            if "VAEntrypointEncSlice" in r.stdout and "H264" in r.stdout:
                return dev
        except Exception:
            pass
    return None


def _get_screen_info() -> tuple[int, int, int]:
    """Retorna (largura, altura, hz) — resolução do monitor primário, Hz do mais rápido."""
    screens = QApplication.screens()
    if not screens:
        return 1920, 1080, 30
    primary = QApplication.primaryScreen() or screens[0]
    geo = primary.geometry()
    hz = max(max(round(s.refreshRate()) for s in screens), 1)
    return geo.width(), geo.height(), hz


def _save_dir() -> Path:
    for d in (Path.home() / "Vídeos", Path.home() / "Videos"):
        if d.exists():
            return d / "EpicPen"
    return Path.home() / "Vídeos" / "EpicPen"


def build_command(
    dest: Path,
    env: str,
    vaapi_dev: str | None,
    _screen_info: tuple[int, int, int] | None = None,
) -> list[str] | None:
    """Monta o comando de gravação. `_screen_info` permite injeção em testes."""
    w, h, fps = _screen_info or _get_screen_info()

    if env == "wayland-wlroots":
        # Melhor: wl-screenrec (DMA-buf, encode inteiramente na GPU)
        if _which("wl-screenrec") and vaapi_dev:
            return [
                _which("wl-screenrec"),
                "--encode-device", vaapi_dev,
                "--codec", "h264",
                "-f", str(dest),
            ]
        # Bom: wf-recorder + VAAPI
        if _which("wf-recorder") and vaapi_dev:
            return [
                _which("wf-recorder"),
                "-c", "h264_vaapi",
                "-d", vaapi_dev,
                "--pixel-format", "yuv420p",
                "-f", str(dest),
            ]
        # Fallback CPU: wf-recorder + libx264 ultrafast
        if _which("wf-recorder"):
            return [
                _which("wf-recorder"),
                "-c", "libx264",
                "--codec-param", "preset=ultrafast",
                "--codec-param", "crf=30",
                "-f", str(dest),
            ]

    elif env in ("wayland-gnome", "wayland-kde"):
        # gpu-screen-recorder usa portal internamente (GNOME + KDE)
        if _which("gpu-screen-recorder"):
            return [
                _which("gpu-screen-recorder"),
                "-w", "screen",
                "-f", str(fps),
                "-c", "h264",
                "-o", str(dest),
            ]
        # Fallback: wf-recorder (funciona no KDE Wayland)
        if _which("wf-recorder"):
            cmd = [_which("wf-recorder"), "-f", str(dest)]
            if vaapi_dev:
                cmd += ["-c", "h264_vaapi", "-d", vaapi_dev, "--pixel-format", "yuv420p"]
            else:
                cmd += ["-c", "libx264",
                        "--codec-param", "preset=ultrafast",
                        "--codec-param", "crf=30"]
            return cmd

    else:  # x11
        ffmpeg = _which("ffmpeg")
        if not ffmpeg:
            return None
        display = os.environ.get("DISPLAY", ":0")
        if vaapi_dev:
            return [
                ffmpeg, "-y",
                "-vaapi_device", vaapi_dev,
                "-f", "x11grab",
                "-framerate", str(fps),
                "-video_size", f"{w}x{h}",
                "-i", f"{display}+0,0",
                "-rtbufsize", "64M",
                "-vf", "format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-b:v", "3000k",
                str(dest),
            ]
        return [
            ffmpeg, "-y",
            "-f", "x11grab",
            "-framerate", str(fps),
            "-video_size", f"{w}x{h}",
            "-i", f"{display}+0,0",
            "-rtbufsize", "64M",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-crf", "30",
            "-pix_fmt", "yuv420p",
            str(dest),
        ]
    return None


class ScreenRecorder(QObject):
    started = pyqtSignal()
    stopped = pyqtSignal(str)   # path do arquivo salvo
    failed  = pyqtSignal(str)   # mensagem de erro

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = QProcess(self)
        self._proc.finished.connect(self._on_finished)
        self._dest: Path | None = None
        self._env   = _detect_env()
        self._vaapi = _detect_vaapi()

    @property
    def is_recording(self) -> bool:
        return self._proc.state() == QProcess.ProcessState.Running

    def start(self) -> bool:
        if self.is_recording:
            return True
        save_dir = _save_dir()
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._dest = save_dir / f"epicpen_rec_{ts}.mp4"
        cmd = build_command(self._dest, self._env, self._vaapi)
        if not cmd:
            self.failed.emit(
                "Nenhuma ferramenta de gravação encontrada.\n"
                "Instale: wf-recorder, wl-screenrec, gpu-screen-recorder ou ffmpeg"
            )
            return False
        self._proc.start(cmd[0], cmd[1:])
        if not self._proc.waitForStarted(3000):
            self.failed.emit(f"Falha ao iniciar: {Path(cmd[0]).name}")
            return False
        self.started.emit()
        return True

    def stop(self):
        if not self.is_recording:
            return
        prog = self._proc.program()
        if prog and "ffmpeg" in prog:
            # ffmpeg para graciosamente ao receber 'q' no stdin
            self._proc.write(b"q")
            self._proc.closeWriteChannel()
        else:
            # wf-recorder / wl-screenrec / gpu-screen-recorder: SIGTERM salva o arquivo
            self._proc.terminate()
        if not self._proc.waitForFinished(8000):
            self._proc.kill()

    def _on_finished(self, _exit_code, _status):
        dest = self._dest
        if dest and dest.exists() and dest.stat().st_size > 0:
            try:
                rel = dest.relative_to(Path.home())
                msg = f"~/{rel}"
            except ValueError:
                msg = str(dest)
            self.stopped.emit(str(dest))
        else:
            self.failed.emit("Gravação falhou ou arquivo vazio.")

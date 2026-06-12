import glob
import json
import os
import shutil
import sys
import signal
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtMultimedia import QVideoFrame
from PyQt6.QtGui import QImage

from hostenv import host_env

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
    """Todos os binários ffmpeg instalados (bundled primeiro).

    Inclui cada caminho de _EXTRA_PATHS além do primeiro do PATH: builds de
    Homebrew/snap costumam vir sem pulse/VAAPI e sombrear o ffmpeg da distro
    que tem tudo — a pontuação do _pick_ffmpeg decide, não a ordem do PATH.
    """
    out: list[str] = []

    def _add(path: Optional[str]):
        if path:
            real = os.path.realpath(path)
            if real not in out and os.access(real, os.X_OK):
                out.append(real)

    appdir = os.environ.get("APPDIR", "")
    if appdir:
        _add(os.path.join(appdir, "usr", "bin", "ffmpeg"))
    _add(shutil.which("ffmpeg"))
    for p in _EXTRA_PATHS:
        _add(os.path.join(p, "ffmpeg"))
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
            capture_output=True, text=True, timeout=5, env=host_env(),
        )
        return "libx264" in r.stdout
    except Exception:
        return False


def _has_aac(ffmpeg: str) -> bool:
    """True se este ffmpeg inclui o encoder AAC."""
    try:
        r = subprocess.run(
            [ffmpeg, "-encoders"],
            capture_output=True, text=True, timeout=5, env=host_env(),
        )
        return " aac " in r.stdout
    except Exception:
        return False


def _has_audio_support(ffmpeg: str) -> bool:
    """True se este ffmpeg captura PulseAudio/PipeWire E encoda AAC."""
    try:
        r = subprocess.run(
            [ffmpeg, "-devices"],
            capture_output=True, text=True, timeout=5, env=host_env(),
        )
        if "pulse" not in r.stdout:
            return False
    except Exception:
        return False
    return _has_aac(ffmpeg)


def _audio_mode(ffmpeg: str) -> Optional[str]:
    """Como capturar áudio com este ffmpeg: 'pulse', 'parec' ou None.

    pulse — o ffmpeg lê os devices diretamente (builds de distro).
    parec — o ffmpeg não tem entrada pulse (build estático bundlado no
    AppImage), mas o parec do sistema captura e entrega PCM cru via pipe.
    """
    if _has_audio_support(ffmpeg):
        return "pulse"
    if shutil.which("parec") and _has_aac(ffmpeg):
        return "parec"
    return None


def _default_audio_devices() -> list[str]:
    """Devices PulseAudio para gravar: [microfone, monitor dos alto-falantes].

    Usa pactl para descobrir os defaults — se o pactl responde, o servidor
    de som (PipeWire/Pulse) está vivo e o ffmpeg vai conseguir conectar.
    Sem pactl, retorna [] (gravação segue sem áudio em vez de falhar tudo).
    """
    def _pactl(*args: str) -> Optional[str]:
        try:
            r = subprocess.run(
                ["pactl", *args], capture_output=True, text=True, timeout=3, env=host_env(),
            )
            out = r.stdout.strip()
            return out if r.returncode == 0 and out else None
        except Exception:
            return None

    devs: list[str] = []
    mic = _pactl("get-default-source")
    if mic:
        devs.append(mic)
    sink = _pactl("get-default-sink")
    if sink:
        monitor = f"{sink}.monitor"
        if monitor not in devs:
            devs.append(monitor)
    return devs


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
            r = subprocess.run(cmd, capture_output=True, timeout=8, env=host_env())
            if r.returncode == 0:
                found = dev
                break
        except Exception:
            continue
    _VAAPI_CACHE[ffmpeg] = found
    return found


def _pick_ffmpeg() -> tuple[Optional[str], Optional[str], bool, Optional[str]]:
    """Escolhe o melhor ffmpeg: (path, vaapi_device, has_x264, audio_mode).

    Pontuação por capacidade: áudio (mic+alto-falantes) pesa mais que VAAPI,
    que pesa mais que libx264. O bundled do AppImage não tem pulse nem VAAPI,
    por isso o ffmpeg do sistema também concorre — quem oferecer mais ganha.
    """
    cands = _ffmpeg_candidates()
    if not cands:
        return None, None, False, None

    best = None
    best_score = -1
    for c in cands:
        vaapi = _probe_vaapi(c)
        audio = _audio_mode(c)
        x264 = _has_libx264(c)
        score = (4 if audio else 0) + (2 if vaapi else 0) + (1 if x264 else 0)
        if score > best_score:
            best_score = score
            best = (c, vaapi, x264, audio)
    return best


# ── Perfil da máquina e estratégia de gravação ────────────────────────────────

def _mem_available_bytes() -> int:
    """RAM disponível (MemAvailable). 2GB como palpite conservador se falhar."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 2 << 30


def _any_fast_disk() -> bool:
    """True se há disco não-rotacional (SSD/NVMe) na máquina."""
    try:
        for rot in glob.glob("/sys/block/*/queue/rotational"):
            name = rot.split("/")[3]
            if name.startswith(("loop", "zram", "ram", "sr", "dm-")):
                continue
            with open(rot) as f:
                if f.read().strip() == "0":
                    return True
    except Exception:
        pass
    return False


def _pick_strategy(vaapi_dev: Optional[str], has_x264: bool, cores: int,
                   raw_bps: int, disk_free: int, disk_fast: bool) -> str:
    """Decide onde a máquina está 'menos pior' para encodar:

    gpu  — VAAPI funcional: encode na GPU, CPU livre (melhor caso).
    cpu  — CPU dá conta do x264 ultrafast em tempo real.
    disk — CPU fraca mas SSD/NVMe com espaço: grava rawvideo num .nut
           intermediário (~zero CPU durante a captura) e re-encoda ao parar.
    """
    if vaapi_dev:
        return "gpu"
    if cores >= 6:
        return "cpu"
    # CPU fraca: rawvideo no disco se for SSD e couberem ≥2.5 min de captura
    if disk_fast and disk_free > raw_bps * 150:
        return "disk"
    return "cpu"


def _queue_frames(frame_bytes: int, mem_available: int) -> int:
    """Profundidade da fila de frames: usa a RAM que sobra como amortecedor.

    Até 25% da RAM disponível (máx. 2GB) em frames — picos do encoder não
    descartam frames em máquinas com memória; mínimo de 4 nas apertadas.
    """
    if frame_bytes <= 0:
        return 4
    budget = min(int(mem_available * 0.25), 2 << 30)
    return max(4, min(budget // frame_bytes, 240))


# Timebase do vídeo: 1000 ticks/s (1ms). Com timestamps wallclock, um timebase
# grosso (1/fps) faz frames lidos em rajada caírem no mesmo tick e o -vsync vfr
# descartá-los como duplicados — medido: 2/3 dos frames perdidos a 144.
_VIDEO_TIMEBASE_FPS = 1000


def _build_ffmpeg_cmd(
    ffmpeg: str, w: int, h: int, fps: int, dest: str, has_x264: bool,
    pix_fmt: str = "rgba",
    vaapi_device: Optional[str] = None,
    raw_intermediate: bool = False,
    crop: Optional[tuple[int, int]] = None,
) -> list[str]:
    """Comando do processo de VÍDEO: rawvideo via stdin → .mkv (ou .nut).

    Só vídeo, nunca áudio: mux ao vivo de vídeo+áudio no ffmpeg CLI trava a
    leitura do pipe em ~23fps (medido) — o áudio roda num processo separado
    e os dois são montados no final com timestamps absolutos (-copyts).

    -vsync vfr preserva os timestamps wallclock sem duplicar frames — é o que
    permite a deduplicação no capture_helper: tela estática gera zero trabalho de
    encode e o player simplesmente segura o último frame.

    raw_intermediate: estratégia disk — copia rawvideo para .nut sem encodar.
    crop: (w, h) reais quando o stride do frame tem padding (w do comando é
    stride/4; o filtro corta de volta para a área visível).
    """
    base = [ffmpeg]
    if vaapi_device and not raw_intermediate:
        base += [
            "-init_hw_device", f"vaapi=va:{vaapi_device}",
            "-filter_hw_device", "va",
        ]
    base += [
        "-use_wallclock_as_timestamps", "1",
        "-f", "rawvideo",
        "-pixel_format", pix_fmt,
        "-video_size", f"{w}x{h}",
        "-framerate", str(_VIDEO_TIMEBASE_FPS),
        "-i", "pipe:0",
    ]

    vf_parts: list[str] = []
    if crop is not None:
        vf_parts.append(f"crop={crop[0]}:{crop[1]}:0:0")

    if raw_intermediate:
        # Estratégia disk: zero encode agora; só memcpy pipe → arquivo .nut
        # (nut preserva timestamps VFR). Re-encode acontece ao parar.
        encode = ["-c:v", "rawvideo"]
    elif vaapi_device:
        # Encode 100% na GPU: hwupload + h264_vaapi. CPU só faz o memcpy do pipe.
        vf_parts += ["format=nv12", "hwupload"]
        encode = [
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

    if vf_parts:
        encode = ["-vf", ",".join(vf_parts)] + encode

    # -copyts preserva a época wallclock no arquivo — é o que permite
    # sincronizar com o áudio (processo separado) na montagem final
    return base + encode + ["-an", "-vsync", "vfr", "-copyts", "-y", dest]


# Cache de filtros disponíveis por binário ffmpeg
_FILTER_CACHE: dict[tuple[str, str], bool] = {}


def _has_filter(ffmpeg: str, name: str) -> bool:
    """True se este build do ffmpeg inclui o filtro de áudio/vídeo dado."""
    key = (ffmpeg, name)
    cached = _FILTER_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        r = subprocess.run(
            [ffmpeg, "-filters"],
            capture_output=True, text=True, timeout=5, env=host_env(),
        )
        ok = f" {name} " in r.stdout
    except Exception:
        ok = False
    _FILTER_CACHE[key] = ok
    return ok


def _build_audio_cmd(ffmpeg: str, devices: list[str], dest: str,
                     duck: bool = False) -> list[str]:
    """Comando do processo de ÁUDIO: pulse (mic e/ou monitor) → .mka.

    Processo separado do vídeo de propósito; timestamps wallclock + -copyts
    preservam a época real de captura para o sync exato na montagem.

    duck: com mic (input 0) + alto-falantes (input 1), o áudio do sistema é
    comprimido pelo sinal do mic (sidechain ducking) — a voz nunca é
    abafada pelo som do jogo, como em mesas de streaming.
    """
    base = [ffmpeg]
    for dev in devices:
        base += [
            "-use_wallclock_as_timestamps", "1",
            "-f", "pulse", "-thread_queue_size", "1024", "-i", dev,
        ]
    if len(devices) >= 2:
        if duck:
            graph = (
                "[0:a]asplit=2[mic][sc];"
                "[1:a][sc]sidechaincompress="
                "threshold=0.05:ratio=8:attack=50:release=400[game];"
                "[mic][game]amix=inputs=2:duration=longest:normalize=0[aout]"
            )
        else:
            # normalize=0 evita cortar o volume dos dois pela metade
            graph = "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[aout]"
        maps = ["-filter_complex", graph, "-map", "[aout]"]
    else:
        maps = ["-map", "0:a"]
    return base + maps + [
        "-c:a", "aac", "-b:a", "160k", "-ac", "2",
        "-copyts", "-y", dest,
    ]


def _ffprobe_path(ffmpeg: str) -> Optional[str]:
    """ffprobe ao lado do ffmpeg escolhido, ou no PATH. None se ausente."""
    cand = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
    if os.access(cand, os.X_OK):
        return cand
    return shutil.which("ffprobe")


def _container_start(ffprobe: str, path: str) -> Optional[float]:
    """start_time (segundos, época wallclock com -copyts) do container."""
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=start_time",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10, env=host_env(),
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def _audio_inputs(audio: Optional[str], audio_skip: float) -> list[str]:
    """Input de áudio na montagem; -ss apara o lead anterior ao 1º frame de vídeo."""
    if not audio:
        return []
    pre = ["-ss", f"{audio_skip:.3f}"] if audio_skip > 0.01 else []
    return pre + ["-i", audio]


def _build_audio_cmd_parec(ffmpeg: str, fds: list[int], dest: str,
                           duck: bool = False) -> list[str]:
    """Comando do ffmpeg lendo PCM cru do parec via fds → .mka.

    Para builds de ffmpeg sem entrada pulse (estático bundlado): um parec
    por device entrega s16le/48k/stereo num pipe; o ffmpeg lê pipe:N.
    fds[0] = mic, fds[1] = monitor (mesma ordem de _default_audio_devices).
    """
    base = [ffmpeg]
    for fd in fds:
        base += [
            "-use_wallclock_as_timestamps", "1",
            "-f", "s16le", "-ar", "48000", "-ac", "2",
            "-thread_queue_size", "1024",
            "-i", f"pipe:{fd}",
        ]
    if len(fds) >= 2:
        if duck:
            graph = (
                "[0:a]asplit=2[mic][sc];"
                "[1:a][sc]sidechaincompress="
                "threshold=0.05:ratio=8:attack=50:release=400[game];"
                "[mic][game]amix=inputs=2:duration=longest:normalize=0[aout]"
            )
        else:
            graph = "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[aout]"
        maps = ["-filter_complex", graph, "-map", "[aout]"]
    else:
        maps = ["-map", "0:a"]
    return base + maps + [
        "-c:a", "aac", "-b:a", "160k", "-ac", "2",
        "-copyts", "-y", dest,
    ]


def _spawn_parec_audio(ffmpeg: str, devices: list[str], dest: str,
                       duck: bool):
    """Sobe parec(s) + ffmpeg encadeados por pipes para capturar áudio.

    Retorna (proc_ffmpeg, [procs_parec]) ou (None, []) em falha. Os parec
    são terminados primeiro no stop — o EOF nos pipes finaliza o ffmpeg
    graciosamente, sem sinal.
    """
    read_fds: list[int] = []
    helpers: list[subprocess.Popen] = []
    try:
        for dev in devices:
            r, w = os.pipe()
            helper = subprocess.Popen(
                ["parec", "--raw", "--format=s16le", "--rate=48000",
                 "--channels=2", f"--device={dev}"],
                stdin=subprocess.DEVNULL,
                stdout=w,
                stderr=subprocess.DEVNULL,
                env=host_env(),
            )
            os.close(w)
            read_fds.append(r)
            helpers.append(helper)

        cmd = _build_audio_cmd_parec(ffmpeg, read_fds, dest, duck=duck)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=read_fds,
            preexec_fn=_lower_priority,
            env=host_env(),
        )
        return proc, helpers
    except OSError:
        for h in helpers:
            try:
                h.kill()
            except OSError:
                pass
        return None, []
    finally:
        for r in read_fds:
            try:
                os.close(r)
            except OSError:
                pass


def _build_remux_cmd(ffmpeg: str, video: str, audio: Optional[str],
                     dest: str, audio_skip: float = 0.0) -> list[str]:
    """Montagem final sem re-encode: vídeo .mkv (+ áudio .mka) → MP4.

    -copyts + avoid_negative_ts make_zero: ambos os arquivos carregam
    timestamps na época wallclock; o shift comum para zero preserva o
    offset real entre áudio e vídeo (sync exato).
    audio_skip: apara o áudio gravado antes do primeiro frame de vídeo
    (o portal demora a entregar o 1º frame; sem o corte, o MP4 abre
    com segundos de tela preta).
    """
    cmd = [ffmpeg, "-i", video] + _audio_inputs(audio, audio_skip)
    cmd += ["-map", "0:v"]
    if audio:
        cmd += ["-map", "1:a", "-c:a", "copy"]
    return cmd + [
        "-c:v", "copy",
        "-copyts", "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart", "-y", dest,
    ]


def _build_transcode_cmd(ffmpeg: str, video: str, audio: Optional[str],
                         dest: str, has_x264: bool,
                         audio_skip: float = 0.0) -> list[str]:
    """Re-encode do .nut intermediário (estratégia disk) para o MP4 final."""
    cmd = [ffmpeg, "-i", video] + _audio_inputs(audio, audio_skip)
    cmd += ["-map", "0:v"]
    if audio:
        cmd += ["-map", "1:a", "-c:a", "copy"]
    if has_x264:
        encode = [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
        ]
    else:
        encode = ["-c:v", "mpeg4", "-q:v", "5"]
    return cmd + encode + [
        "-copyts", "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart", "-y", dest,
    ]


def _helper_cmd(screen_name: str) -> list[str]:
    """Comando do processo auxiliar de captura (mesmo intérprete/ambiente)."""
    helper = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "capture_helper.py",
    )
    return [sys.executable, helper, screen_name]


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
        # A captura roda num PROCESSO auxiliar (capture_helper.py): o Qt
        # nunca fecha a sessão de ScreenCast do portal enquanto o processo
        # vive (deleteLater/sip.delete não encerram o stream PipeWire —
        # medido com pw-dump). O fim do helper derruba a conexão DBus e o
        # portal fecha a sessão — sem ícone de transmissão pendurado no KDE.
        self._helper: Optional[subprocess.Popen] = None
        self._pump_thread: Optional[threading.Thread] = None

        self._proc: Optional[subprocess.Popen] = None
        self._dest: Optional[Path] = None
        self._active = False

        # Configurados em start(); _rec_w/_rec_h são refinados pelo header
        # do helper (resolução física do compositor, não a lógica do Qt)
        self._ffmpeg_path: Optional[str] = None
        self._rec_w = 0
        self._rec_h = 0
        self._rec_fps = 0
        self._rec_has_x264 = False
        self._rec_vaapi_dev: Optional[str] = None
        self._rec_audio_devs: list[str] = []
        self._rec_strategy = "cpu"
        self._capture_dest: Optional[Path] = None  # .mkv (ou .nut em disk)
        self._audio_dest: Optional[Path] = None    # .mka do processo de áudio
        self._audio_proc: Optional[subprocess.Popen] = None
        self._audio_helpers: list[subprocess.Popen] = []  # parec(s)
        self._frame_nbytes = 0                     # stride×h — fatia exata p/ pipe

    @property
    def is_recording(self) -> bool:
        return self._active

    def start(self, screen=None) -> bool:
        """Inicia a gravação. screen: monitor a capturar (default: maior Hz)."""
        if self._active:
            return True

        ffmpeg, vaapi_dev, has_x264, audio_mode = _pick_ffmpeg()
        if not ffmpeg:
            self.failed.emit(
                "ffmpeg não encontrado.\n"
                "Instale com: sudo dnf install ffmpeg  (Fedora)\n"
                "             sudo apt install ffmpeg  (Ubuntu/Debian)"
            )
            return False

        if screen is None:
            screen = _best_screen()
        if screen is None:
            self.failed.emit("Nenhuma tela detectada.")
            return False

        geo = screen.geometry()
        dpr = screen.devicePixelRatio() or 1.0
        # Estimativa em pixels físicos (refinada no primeiro frame real)
        self._rec_w = int(geo.width() * dpr)
        self._rec_h = int(geo.height() * dpr)
        # Hz nativo do monitor, sem teto artificial — máximo que a máquina dá
        self._rec_fps = max(1, min(int(round(screen.refreshRate())), 240))
        self._ffmpeg_path = ffmpeg
        self._rec_has_x264 = has_x264
        self._rec_vaapi_dev = vaapi_dev
        self._rec_audio_devs = _default_audio_devices() if audio_mode else []
        self._proc = None
        self._frame_nbytes = 0

        save_dir = _save_dir()
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._dest = save_dir / f"epicpen_rec_{ts}.mp4"

        # Estratégia adaptativa: encoda onde a máquina está menos pior
        raw_bps = self._rec_w * self._rec_h * 4 * self._rec_fps
        try:
            disk_free = shutil.disk_usage(save_dir).free
        except OSError:
            disk_free = 0
        self._rec_strategy = _pick_strategy(
            vaapi_dev, has_x264, os.cpu_count() or 2,
            raw_bps, disk_free, _any_fast_disk(),
        )
        ext = ".nut" if self._rec_strategy == "disk" else ".mkv"
        self._capture_dest = save_dir / f".epicpen_rec_{ts}{ext}"

        # Áudio em processo separado, iniciado já: o sync com o vídeo é por
        # timestamp absoluto, não por ordem de partida
        self._audio_dest = None
        self._audio_proc = None
        self._audio_helpers = []
        if self._rec_audio_devs and audio_mode:
            self._audio_dest = save_dir / f".epicpen_rec_{ts}.mka"
            duck = (len(self._rec_audio_devs) >= 2
                    and _has_filter(ffmpeg, "sidechaincompress"))
            if audio_mode == "parec":
                self._audio_proc, self._audio_helpers = _spawn_parec_audio(
                    ffmpeg, self._rec_audio_devs, str(self._audio_dest), duck,
                )
            else:
                acmd = _build_audio_cmd(
                    ffmpeg, self._rec_audio_devs, str(self._audio_dest),
                    duck=duck,
                )
                try:
                    self._audio_proc = subprocess.Popen(
                        acmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=_lower_priority,
                        env=host_env(),
                    )
                except OSError:
                    self._audio_proc = None
            if self._audio_proc is None:
                self._audio_dest = None
                self._audio_helpers = []

        # Processo auxiliar de captura: a sessão de portal morre com ele
        try:
            self._helper = subprocess.Popen(
                _helper_cmd(screen.name()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            self._stop_audio()
            self._cleanup_temp(self._audio_dest)
            self.failed.emit(f"Falha ao iniciar captura: {e}")
            return False

        self._active = True
        self._pump_thread = threading.Thread(
            target=self._pump_loop, daemon=True, name="epicpen-pump",
        )
        self._pump_thread.start()
        self.started.emit()
        return True

    def _pump_loop(self):
        """Thread: lê o header + frames do helper e bombeia para o ffmpeg.

        read/write de pipes liberam a GIL — o custo por frame na UI é zero.
        A deduplicação acontece no helper; aqui é cópia cega de bytes.
        """
        helper = self._helper
        try:
            header_line = helper.stdout.readline()
            header = json.loads(header_line)
            w = int(header["w"])
            h = int(header["h"])
            stride = int(header["stride"])
            pix_fmt = str(header["pix_fmt"])
        except Exception:
            return  # helper morreu antes do 1º frame — stop() reporta

        if not self._active:
            return
        if not self._start_ffmpeg(w, h, stride, pix_fmt):
            return

        n = self._frame_nbytes
        read = helper.stdout.read
        write = self._proc.stdin.write
        while True:
            data = read(n)
            if not data or len(data) < n:
                break  # EOF — helper terminou
            try:
                write(data)
            except (BrokenPipeError, OSError, ValueError):
                break
        try:
            self._proc.stdin.close()
        except (OSError, ValueError):
            pass

    def _start_ffmpeg(self, w: int, h: int, stride: int, pix_fmt: str) -> bool:
        """Inicia o processo ffmpeg com a geometria do header do helper.

        O comando usa stride/4 como largura (rawvideo é empacotado) e um
        filtro crop devolve a área visível quando há padding no stride.
        """
        if w <= 0 or h <= 0:
            w, h = self._rec_w, self._rec_h
        if stride <= 0:
            stride = w * 4

        src_w = stride // 4
        crop = (w, h) if src_w != w else None
        self._rec_w, self._rec_h = w, h
        self._frame_nbytes = stride * h

        cmd = _build_ffmpeg_cmd(
            self._ffmpeg_path,
            src_w, h, self._rec_fps,
            str(self._capture_dest),
            self._rec_has_x264,
            pix_fmt,
            vaapi_device=self._rec_vaapi_dev if self._rec_strategy == "gpu" else None,
            raw_intermediate=(self._rec_strategy == "disk"),
            crop=crop,
        )
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=_lower_priority, env=host_env(),
            )
        except OSError as e:
            self.failed.emit(f"Falha ao iniciar ffmpeg: {e}")
            return False

        # Pipes de 1MB: menos syscalls a 100+ MB/s de rawvideo
        try:
            import fcntl
            F_SETPIPE_SZ = 1031  # Linux
            fcntl.fcntl(self._proc.stdin.fileno(), F_SETPIPE_SZ, 1 << 20)
            if self._helper is not None:
                fcntl.fcntl(self._helper.stdout.fileno(), F_SETPIPE_SZ, 1 << 20)
        except Exception:
            pass
        return True

    def stop(self):
        if not self._active:
            return
        self._active = False

        # Encerra o helper de captura: o processo morre → portal fecha a
        # sessão de ScreenCast → ícone de transmissão some do KDE
        if self._helper is not None:
            try:
                self._helper.terminate()
            except (ProcessLookupError, OSError):
                pass
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=5)
            self._pump_thread = None
        if self._helper is not None:
            try:
                self._helper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._helper.kill()
                self._helper.wait()
            self._helper = None

        self._stop_audio()

        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

            captured = self._capture_dest
            dest = self._dest
            self._proc = None

            if not (captured and captured.exists() and captured.stat().st_size > 0):
                self._cleanup_temp(self._audio_dest)
                self.failed.emit("Gravação falhou ou arquivo vazio.")
            else:
                # Montagem final (remux instantâneo ou transcode na estratégia
                # disk) em background; stopped é emitido ao concluir
                threading.Thread(
                    target=self._assemble,
                    args=(captured, self._audio_dest, dest),
                    daemon=True, name="epicpen-assemble",
                ).start()
        else:
            self._cleanup_temp(self._audio_dest)
            self.failed.emit("Nenhum frame capturado.")

    def _stop_audio(self):
        """Encerra a captura de áudio: parec primeiro (EOF nos pipes finaliza
        o ffmpeg graciosamente); sem helpers, SIGINT (input ao vivo nunca
        termina sozinho)."""
        for h in self._audio_helpers:
            try:
                h.terminate()
            except (ProcessLookupError, OSError):
                pass
        if self._audio_proc is not None:
            if not self._audio_helpers:
                try:
                    self._audio_proc.send_signal(signal.SIGINT)
                except (ProcessLookupError, OSError):
                    pass
            try:
                self._audio_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._audio_proc.kill()
                self._audio_proc.wait()
            self._audio_proc = None
        for h in self._audio_helpers:
            try:
                h.wait(timeout=3)
            except subprocess.TimeoutExpired:
                h.kill()
                h.wait()
        self._audio_helpers = []

    @staticmethod
    def _cleanup_temp(path: Optional[Path]):
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass

    def _assemble(self, video: Path, audio: Optional[Path], dest: Path):
        """Monta o MP4 final a partir das capturas de vídeo e áudio."""
        audio_ok = audio is not None and audio.exists() and audio.stat().st_size > 0
        audio_arg = str(audio) if audio_ok else None

        # Apara o áudio gravado antes do 1º frame de vídeo (latência do portal)
        audio_skip = 0.0
        if audio_ok:
            probe = _ffprobe_path(self._ffmpeg_path)
            if probe:
                vs = _container_start(probe, str(video))
                as_ = _container_start(probe, str(audio))
                if vs is not None and as_ is not None and vs > as_:
                    audio_skip = vs - as_

        if self._rec_strategy == "disk":
            cmd = _build_transcode_cmd(
                self._ffmpeg_path, str(video), audio_arg, str(dest),
                self._rec_has_x264, audio_skip=audio_skip,
            )
        else:
            cmd = _build_remux_cmd(
                self._ffmpeg_path, str(video), audio_arg, str(dest),
                audio_skip=audio_skip,
            )
        try:
            r = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                preexec_fn=_lower_priority, env=host_env(), timeout=3600,
            )
        except Exception:
            self.failed.emit(f"Montagem do vídeo falhou; captura mantida em {video}")
            return
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            self._cleanup_temp(video)
            self._cleanup_temp(audio if audio_ok else None)
            self.stopped.emit(str(dest))
        else:
            self.failed.emit(f"Montagem do vídeo falhou; captura mantida em {video}")

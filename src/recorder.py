import glob
import json
import os
import shutil
import sys
import signal
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtMultimedia import QVideoFrame
from PyQt6.QtGui import QImage

import portalcast
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

# Sentinela p/ "posição do monitor desconhecida" no sinal region_needed.
NOPOS = -2147483648


def _build_ffmpeg_cmd(
    ffmpeg: str, w: int, h: int, fps: int, dest: str, has_x264: bool,
    pix_fmt: str = "rgba",
    vaapi_device: Optional[str] = None,
    raw_intermediate: bool = False,
    crop: Optional[tuple] = None,
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
        # crop = (w, h[, x, y]) — recorte da área visível. x/y omitidos = 0
        # (usado para descartar padding de stride); com x/y = recorte de região.
        cw, ch = crop[0], crop[1]
        cx = crop[2] if len(crop) > 2 else 0
        cy = crop[3] if len(crop) > 3 else 0
        vf_parts.append(f"crop={cw}:{ch}:{cx}:{cy}")

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


def _build_audio_cmd(ffmpeg: str, devices: list[str], dest: str) -> list[str]:
    """Comando do processo de ÁUDIO: pulse (mic e/ou monitor) → .mka.

    Processo separado do vídeo de propósito; timestamps wallclock + -copyts
    preservam a época real de captura para o sync exato na montagem.

    Mix sem ducking: mic e som do sistema entram em volume integral
    (normalize=0). Sidechain ducking abafava a voz quando outro software
    (YouTube etc.) tocava — captação fiel dos dois é o comportamento certo.
    """
    base = [ffmpeg]
    for dev in devices:
        base += [
            "-use_wallclock_as_timestamps", "1",
            "-f", "pulse", "-thread_queue_size", "1024", "-i", dev,
        ]
    if len(devices) >= 2:
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


def _build_audio_cmd_parec(ffmpeg: str, fds: list[int], dest: str) -> list[str]:
    """Comando do ffmpeg lendo PCM cru do parec via fds → .mka.

    Para builds de ffmpeg sem entrada pulse (estático bundlado): um parec
    por device entrega s16le/48k/stereo num pipe; o ffmpeg lê pipe:N.
    fds[0] = mic, fds[1] = monitor (mesma ordem de _default_audio_devices).
    Mix sem ducking — ver _build_audio_cmd.
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
        graph = "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[aout]"
        maps = ["-filter_complex", graph, "-map", "[aout]"]
    else:
        maps = ["-map", "0:a"]
    return base + maps + [
        "-c:a", "aac", "-b:a", "160k", "-ac", "2",
        "-copyts", "-y", dest,
    ]


def _parec_cmd(dev: str) -> list[str]:
    """parec → s16le/48k/stereo cru no stdout (device pulse: mic ou monitor)."""
    return ["parec", "--raw", "--format=s16le", "--rate=48000",
            "--channels=2", f"--device={dev}"]


def _pwrecord_tap_cmd(cap_name: str) -> list[str]:
    """pw-record como nó de captura ISOLADO (autoconnect=false): não conecta a
    nada sozinho. A app é ligada manualmente às portas deste nó via pw-link
    (_link_app_capture) → captura SÓ o áudio dela, sem desviar o playback.
    Emite s16le/48k/stereo cru no stdout."""
    return ["pw-record",
            "--properties", f"{{ node.name={cap_name} node.autoconnect=false }}",
            "--rate", "48000", "--channels", "2", "--format", "s16", "-"]


def _pw_node_ports(*, node_id: Optional[int] = None, node_name: Optional[str] = None,
                   direction: str = "out") -> dict:
    """{canal: port_id} das portas de áudio de um nó (por id OU nome), na
    direção dada. Usa pw-dump. {} se pw-dump faltar/erro."""
    if not shutil.which("pw-dump"):
        return {}
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True,
                           timeout=4, env=host_env())
        data = json.loads(r.stdout)
    except Exception:
        return {}
    # Resolve nome → node.id se preciso.
    if node_id is None and node_name is not None:
        for o in data:
            if o.get("type") == "PipeWire:Interface:Node":
                if o.get("info", {}).get("props", {}).get("node.name") == node_name:
                    node_id = o["id"]
                    break
    if node_id is None:
        return {}
    ports: dict = {}
    for o in data:
        if o.get("type") != "PipeWire:Interface:Port":
            continue
        p = o.get("info", {}).get("props", {})
        if int(p.get("node.id", -1)) != int(node_id):
            continue
        if str(p.get("port.direction")) != direction:
            continue
        ch = p.get("audio.channel") or p.get("port.name")
        if ch:
            ports[ch] = o["id"]
    return ports


def _app_node_ids(app_key: int) -> list[int]:
    """node_ids da aplicação. app_key>0 = PID → TODOS os streams daquele
    processo (o Teams tem vários); app_key<0 = um nó só (-node_id)."""
    if app_key < 0:
        return [-app_key]
    return [n["id"] for n in _audio_stream_nodes() if n["pid"] == app_key]


def _link_app_capture(app_key: int, cap_name: str) -> None:
    """Liga as portas de saída de TODOS os streams da app (app_key) às portas de
    entrada do nó de captura (cap_name) via pw-link, casando por canal. Vários
    streams → o PipeWire mixa no mesmo canal. Espera o nó de captura aparecer
    (~1.5s). Captura isolada da app; ao matar o pw-record os links somem."""
    cap = {}
    for _ in range(30):
        cap = _pw_node_ports(node_name=cap_name, direction="in")
        if cap:
            break
        time.sleep(0.05)
    if not cap:
        return
    for node_id in _app_node_ids(app_key):
        for ch, out_pid in _pw_node_ports(node_id=node_id, direction="out").items():
            in_pid = cap.get(ch)
            if in_pid is None:
                continue
            try:
                subprocess.run(["pw-link", str(out_pid), str(in_pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=3, env=host_env())
            except Exception:
                pass


def _audio_stream_nodes() -> list[dict]:
    """Nós Stream/Output/Audio atuais (via pw-dump), cada um como dict com
    id, pid, e nomes úteis. [] se pw-dump faltar/erro."""
    if not shutil.which("pw-dump"):
        return []
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True,
                           timeout=4, env=host_env())
        data = json.loads(r.stdout)
    except Exception:
        return []
    out = []
    for obj in data:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        p = obj.get("info", {}).get("props", {})
        if p.get("media.class") != "Stream/Output/Audio":
            continue
        pid = p.get("application.process.id")
        out.append({
            "id": int(obj["id"]),
            "pid": int(pid) if pid is not None else None,
            "name": (p.get("application.name") or p.get("node.description")
                     or p.get("node.name") or "App"),
            "binary": p.get("application.process.binary") or "",
            "fid": p.get("application.id") or "",
        })
    return out


def list_audio_apps() -> list[tuple[str, int]]:
    """Aplicações tocando áudio agora: [(rótulo, app_key), ...].

    UMA entrada por APLICAÇÃO (agrupada por PID), não por stream — o Teams gera
    vários streams "Chromium" e o usuário quer capturar "o Teams" inteiro. Ao
    gravar, TODOS os streams daquele PID são ligados ao nó de captura (o PipeWire
    mixa). app_key = PID (>0); nós sem PID entram individualmente como -node_id.
    O PID é estável (não fica stale como o node_id)."""
    if not shutil.which("pw-record"):
        return []
    nodes = _audio_stream_nodes()
    apps: dict = {}          # key -> label (uma por app)
    order: list = []
    for n in nodes:
        pid = n["pid"]
        key = pid if pid is not None else -n["id"]
        if key in apps:
            continue
        base = n["name"]
        # Nome mais amigável quando genérico (Chromium/Electron): usa o flatpak.
        if base.lower() in ("chromium", "chrome", "electron") and n["fid"]:
            base = n["fid"].split(".")[-1]
        elif base.lower() in ("chromium", "chrome", "electron") and n["binary"]:
            base = n["binary"]
        apps[key] = base
        order.append(key)
    return [(apps[k], k) for k in order]


def _spawn_parec_audio(ffmpeg: str, devices: list[str], dest: str):
    """Compat: captura via parec para cada device pulse."""
    return _spawn_pipe_audio(ffmpeg, [_parec_cmd(d) for d in devices], dest)


def _spawn_pipe_audio(ffmpeg: str, sources: list, dest: str):
    """Sobe os capturadores (parec/pw-record) + ffmpeg por pipes.

    Cada item de `sources` é:
      • uma lista argv (parec/pw-record simples), ou
      • ("tap", app_node_id, cap_name, argv) — nó de captura isolado; após o
        spawn, liga as portas da app (pw-link) para capturar SÓ ela.
    Todos emitem s16le/48k/stereo no stdout; o ffmpeg lê cada pipe e mixa (amix).
    Retorna (proc_ffmpeg, [procs]) ou (None, []).
    """
    read_fds: list[int] = []
    helpers: list[subprocess.Popen] = []
    try:
        for src in sources:
            is_tap = isinstance(src, tuple) and src and src[0] == "tap"
            cmd = src[3] if is_tap else src
            r, w = os.pipe()
            helper = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=w,
                stderr=subprocess.DEVNULL,
                env=host_env(),
            )
            os.close(w)
            read_fds.append(r)
            helpers.append(helper)
            if is_tap:
                # Liga a app ao nó de captura recém-criado (isolamento).
                _link_app_capture(src[1], src[2])

        cmd = _build_audio_cmd_parec(ffmpeg, read_fds, dest)
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
    # (source, token): o portal devolveu um restore_token para gravar a mesma
    # fonte sem novo seletor da próxima vez. A UI persiste no config.
    restore_token_ready = pyqtSignal(str, str)
    # Modo região: o 1º frame do MONITOR já capturado (após o seletor do portal)
    # vai para a UI como fundo da seleção do retângulo. A UI responde com
    # provide_region(). (data, w, h, stride, pix_fmt, pos_x, pos_y) — pos_* é a
    # posição do monitor no layout (do portal) p/ a UI achar o monitor certo;
    # NOPOS quando o backend não fornece.
    region_needed = pyqtSignal(bytes, int, int, int, str, int, int)

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
        # True enquanto o teardown (join/wait do ffmpeg) roda em background.
        # start() recusa reentrada aqui: sem isso um novo helper subiria antes
        # de a sessão de ScreenCast anterior fechar (KDE empilha "câmeras").
        self._stopping = False
        self._stop_thread: Optional[threading.Thread] = None

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
        # Fonte da captura: "monitor" (QScreenCapture silencioso ou portal) ou
        # "window" (só portal). show_cursor False força o portal.
        self._rec_source = "monitor"
        self._rec_show_cursor = True
        self._rec_restore_token: Optional[str] = None
        # Crop do modo "region", em pixels do FRAME: (cw, ch, cx, cy). Definido
        # pela UI (provide_region) após o seletor do portal e a seleção do
        # retângulo sobre o 1º frame. None = grava o monitor inteiro.
        self._rec_crop: Optional[tuple] = None
        self._region_event = threading.Event()
        self._region_crop: Optional[tuple] = None
        self._region_cancelled = False
        # Fonte real pedida ao portal ("monitor"/"window"); "region" vira monitor.
        self._portal_source = "monitor"
        self._use_portal = False
        self._capture_dest: Optional[Path] = None  # .mkv (ou .nut em disk)
        self._audio_dest: Optional[Path] = None    # .mka do processo de áudio
        self._audio_proc: Optional[subprocess.Popen] = None
        self._audio_helpers: list[subprocess.Popen] = []  # parec(s)
        self._frame_nbytes = 0                     # stride×h — fatia exata p/ pipe

    @property
    def is_recording(self) -> bool:
        return self._active

    def provide_region(self, crop: Optional[tuple], cancelled: bool = False):
        """UI responde ao region_needed: crop = (cw,ch,cx,cy) em px do frame,
        None = monitor inteiro, cancelled = abortar a gravação. Libera a thread
        do pump que espera em _region_event."""
        self._region_crop = crop
        self._region_cancelled = bool(cancelled)
        self._region_event.set()

    def start(self, screen=None, source: str = "monitor",
              show_cursor: bool = True,
              restore_token: Optional[str] = None,
              record_mic: bool = True,
              audio_source: str = "system",
              audio_app_node: Optional[int] = None,
              audio_app_name: Optional[str] = None) -> bool:
        """Inicia a gravação.

        record_mic: inclui o microfone no áudio (mixado).
        audio_source: "system" (todo o som do PC), "app" (só a aplicação em
        audio_app_node, via pw-record) ou "none" (sem som do PC — só mic se on).
        audio_app_node: node PipeWire da aplicação (ver list_audio_apps).

        screen: monitor a capturar no caminho silencioso (default: maior Hz).
        source: "monitor" (tela), "window" (uma janela) ou "region" (retângulo).
        show_cursor: False oculta o cursor (exige portal).
        restore_token: token do portal p/ gravar a mesma fonte sem novo seletor.
        Modo "region": captura o MONITOR (composto, inclui o overlay do EpicPen)
        e recorta no retângulo escolhido DEPOIS, sobre o 1º frame (ver
        region_needed/_pump_loop). Cross-desktop (não usa API de compositor).

        Gravar janela ou ocultar o cursor no Wayland só é possível pelo portal
        ScreenCast (org.freedesktop.portal.ScreenCast). Monitor com cursor
        visível continua no QScreenCapture silencioso (sem seletor).
        """
        if self._active:
            return True
        if self._stopping:
            # Gravação anterior ainda finalizando (ffmpeg flush): iniciar agora
            # subiria um 2º stream ScreenCast antes de o 1º fechar.
            self.failed.emit("Aguarde: finalizando a gravação anterior…")
            return False

        self._rec_source = source
        self._rec_show_cursor = show_cursor
        self._rec_restore_token = restore_token
        # Modo região: o retângulo é escolhido DEPOIS do seletor do portal,
        # sobre o 1º frame (ver _pump_loop). Zera o estado da negociação.
        self._rec_crop = None
        self._region_crop = None
        self._region_cancelled = False
        self._region_event.clear()
        # "region" captura o MONITOR (composto) e recorta depois — o portal só
        # conhece monitor/janela, então a fonte real pedida ao portal é monitor.
        self._portal_source = "monitor" if source == "region" else source
        self._use_portal = portalcast.needs_portal(source, show_cursor)
        if self._use_portal and not portalcast.available():
            self.failed.emit(
                "Gravar uma janela ou ocultar o cursor precisa do GStreamer "
                "com plugin PipeWire.\nInstale: gstreamer1-plugins-good, "
                "gstreamer1-plugin-pipewire e python3-gobject."
            )
            return False

        ffmpeg, vaapi_dev, has_x264, audio_mode = _pick_ffmpeg()
        if not ffmpeg:
            self.failed.emit(
                "ffmpeg não encontrado.\n"
                "Instale com: sudo dnf install ffmpeg  (Fedora)\n"
                "             sudo apt install ffmpeg  (Ubuntu/Debian)"
            )
            return False

        from screens import screen_alive
        if screen is not None and not screen_alive(screen):
            screen = None   # QScreen pendente (troca de monitor) — recalcula
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
        # Devices default (mic = default-source; monitor = som do sistema).
        _devs = _default_audio_devices() if audio_mode else []
        self._mic_dev = next((d for d in _devs if not d.endswith(".monitor")), None)
        self._mon_dev = next((d for d in _devs if d.endswith(".monitor")), None)
        self._rec_audio_source = audio_source
        self._rec_audio_app_node = audio_app_node
        self._rec_record_mic = record_mic
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
        if audio_mode:
            self._audio_dest = save_dir / f".epicpen_rec_{ts}.mka"
            mic = self._mic_dev if record_mic else None
            # audio_app_node é a app_key (PID; estável). O tap liga TODOS os
            # streams da app ao nó de captura — ver _link_app_capture.
            if audio_source == "app" and audio_app_node is not None and shutil.which("pw-record"):
                # Captura ISOLADA da app: nó de captura sem autoconnect + pw-link
                # de todos os streams da app. Mic via parec. Ambos → pipes → mix.
                cap_name = f"EpicPenTap{os.getpid()}"
                sources: list = [_parec_cmd(mic)] if mic else []
                sources.append(("tap", int(audio_app_node), cap_name,
                                _pwrecord_tap_cmd(cap_name)))
                self._audio_proc, self._audio_helpers = _spawn_pipe_audio(
                    ffmpeg, sources, str(self._audio_dest),
                )
            else:
                # system / none: caminho pulse/parec com a lista filtrada.
                chosen = ([mic] if mic else [])
                if audio_source == "system" and self._mon_dev:
                    chosen.append(self._mon_dev)
                if not chosen:
                    self._audio_dest = None
                elif audio_mode == "parec":
                    self._audio_proc, self._audio_helpers = _spawn_parec_audio(
                        ffmpeg, chosen, str(self._audio_dest),
                    )
                else:
                    acmd = _build_audio_cmd(ffmpeg, chosen, str(self._audio_dest))
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

        # Processo auxiliar de captura: a sessão de portal morre com ele.
        # Portal (janela/ocultar cursor): portal_capture_helper + GStreamer, com
        # stderr capturado p/ surfacar o motivo se o seletor for cancelado.
        # Monitor com cursor: QScreenCapture silencioso (capture_helper).
        if self._use_portal:
            cmd = portalcast.helper_cmd(
                self._portal_source, self._rec_show_cursor,
                self._rec_restore_token,
                persist=(self._rec_source == "region"),
            )
            helper_stderr = subprocess.PIPE
            helper_env = portalcast.helper_env()
        else:
            cmd = _helper_cmd(screen.name())
            helper_stderr = subprocess.DEVNULL
            helper_env = None
        try:
            self._helper = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=helper_stderr,
                env=helper_env,
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
            # Helper morreu antes do 1º frame: seletor do portal cancelado,
            # portal indisponível ou falha de captura. Reporta e limpa —
            # sem isto o botão ficava preso em "gravando".
            self._fail_before_start()
            return

        # Portal devolveu um restore_token: a UI persiste p/ gravar a mesma
        # fonte sem novo seletor. Emitido antes de qualquer frame.
        token = header.get("restore_token")
        if token:
            self.restore_token_ready.emit(self._rec_source, str(token))

        if not self._active:
            return

        # Modo região: já temos o monitor (seletor do portal passou). Lê o 1º
        # frame, manda para a UI como fundo, e ESPERA o retângulo. Só então
        # inicia o ffmpeg com o crop. Ordem final: seletor do portal → desenho
        # da região → gravação (o que o usuário pediu).
        first = None
        if self._rec_source == "region":
            first = helper.stdout.read(stride * h)
            if not first or len(first) < stride * h:
                self._fail_before_start()
                return
            self._region_crop = None
            self._region_cancelled = False
            self._region_event.clear()
            px = int(header.get("pos_x", NOPOS))
            py = int(header.get("pos_y", NOPOS))
            self.region_needed.emit(bytes(first), w, h, stride, pix_fmt, px, py)
            got = self._region_event.wait(timeout=180)
            if not self._active:
                return
            if not got or self._region_cancelled:
                self._abort_region()
                return
            self._rec_crop = self._region_crop  # (cw,ch,cx,cy) ou None (inteiro)

        if not self._start_ffmpeg(w, h, stride, pix_fmt):
            return

        n = self._frame_nbytes
        read = helper.stdout.read
        write = self._proc.stdin.write
        if first is not None:
            try:
                write(first)
            except (BrokenPipeError, OSError, ValueError):
                pass
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

    def _abort_region(self):
        """Cancela a gravação em modo região (usuário fechou o seletor de área)
        antes de o ffmpeg iniciar. Encerra helper/áudio e reporta. Roda na
        thread do pump."""
        if not self._active:
            return
        self._active = False
        if self._helper is not None:
            try:
                self._helper.terminate()
            except (ProcessLookupError, OSError):
                pass
        self._stop_audio()
        self._cleanup_temp(self._audio_dest)
        self._audio_dest = None
        self.failed.emit("Seleção de região cancelada.")

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
        # Modo região: crop já vem em px do FRAME (selecionado sobre o 1º frame).
        # Só clampa aos limites e força dimensões pares (h264/yuv420p exige).
        if self._rec_crop is not None:
            cw, ch, cx, cy = self._rec_crop
            cx = max(0, min(int(cx), w - 2))
            cy = max(0, min(int(cy), h - 2))
            cw = max(2, min(int(cw) & ~1, w - cx))
            ch = max(2, min(int(ch) & ~1, h - cy))
            crop = (cw, ch, cx, cy)
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

    def _fail_before_start(self):
        """Captura abortada antes do 1º frame (seletor do portal cancelado,
        portal indisponível, falha de captura). Reporta o motivo e libera
        áudio/helper. Roda na thread do pump; o guard evita corrida com stop()."""
        if not self._active:
            return
        self._active = False

        reason = ""
        helper = self._helper
        if helper is not None:
            try:
                if helper.stderr is not None:
                    err = helper.stderr.read() or b""
                    if err:
                        try:
                            last = err.decode().strip().splitlines()[-1]
                            reason = json.loads(last).get("error", "")
                        except Exception:
                            reason = err.decode(errors="replace").strip()
            except Exception:
                pass
            try:
                helper.wait(timeout=3)
            except Exception:
                try:
                    helper.kill()
                except Exception:
                    pass
            self._helper = None

        self._stop_audio()
        self._cleanup_temp(self._audio_dest)
        self._audio_dest = None

        if not reason:
            reason = ("Nenhuma janela selecionada."
                      if self._rec_source == "window"
                      else "Gravação cancelada.")
        self.failed.emit(reason)

    def stop(self):
        """Encerra a gravação sem bloquear a thread da GUI.

        O teardown (terminate + join do pump + wait do ffmpeg/áudio, até
        dezenas de segundos numa gravação longa) roda numa thread própria —
        antes rodava na thread da GUI e congelava a toolbar ao salvar. Os
        signals started/stopped/failed já são entregues via QueuedConnection
        quando emitidos de outra thread (o QObject vive na thread da GUI).
        """
        if not self._active:
            return
        self._active = False
        self._stopping = True
        self._stop_thread = threading.Thread(
            target=self._teardown, daemon=True, name="epicpen-stop",
        )
        self._stop_thread.start()

    def _teardown(self):
        try:
            self._teardown_impl()
        finally:
            self._stopping = False

    def _teardown_impl(self):
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

    def kill_all(self):
        """Encerramento imediato (chamado no aboutToQuit do app): mata helper de
        captura, ffmpeg e capturadores de áudio já. Sem isso, fechar o app com
        uma gravação ativa deixa pw-record/parec pendurados (zumbis no PipeWire),
        já que o teardown normal (stop) só roda ao parar a gravação."""
        self._active = False
        for proc in (self._helper, self._proc, self._audio_proc):
            if proc is not None:
                try:
                    proc.terminate()
                except (ProcessLookupError, OSError):
                    pass
        for h in self._audio_helpers:
            try:
                h.terminate()
            except (ProcessLookupError, OSError):
                pass

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

"""Detecção e comando do helper de captura via portal ScreenCast.

Isola do recorder.py a decisão de quando usar o caminho do portal
(portal_capture_helper.py) e se o ambiente o suporta.

No AppImage o GStreamer + PyGObject ficam num bundle isolado (usr/lib/
gstreamer-bundle/), usado SÓ pelo subprocesso helper via env dedicado — o
processo Qt principal não carrega o glib do bundle (evita conflito com o Qt).
Por isso a sondagem de disponibilidade roda o próprio helper com --check em
vez de importar gi no processo principal.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HELPER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "portal_capture_helper.py",
)

_gst_ok: bool | None = None
# Interpretador escolhido para o helper do portal: (python, env). O helper só
# precisa de gi + Gst + pipewiresrc (nada de PyQt), então qualquer python com
# esse stack serve — inclusive o python3 do sistema quando o bundle do AppImage
# falta (build fora do CI Ubuntu). Definido no probe de gstreamer_available().
_helper_interp: "tuple[str, dict] | None" = None


def _bundle_dir() -> str | None:
    """Diretório do bundle GStreamer isolado no AppImage, se presente."""
    appdir = os.environ.get("APPDIR")
    if not appdir:
        return None
    bundle = os.path.join(appdir, "usr", "lib", "gstreamer-bundle")
    return bundle if os.path.isdir(bundle) else None


def _bundle_env() -> dict:
    """Ambiente que aponta gi/Gst para o bundle isolado do AppImage.

    LD_LIBRARY_PATH/GI_TYPELIB_PATH/GST_PLUGIN_PATH → bundle; GST_REGISTRY
    gravável (o AppImage é somente-leitura). Sem bundle, devolve o ambiente
    do sistema inalterado.
    """
    env = dict(os.environ)
    bundle = _bundle_dir()
    if bundle:
        typelibs = os.path.join(bundle, "girepository-1.0")
        plugins = os.path.join(bundle, "gstreamer-1.0")
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            [bundle] + ([env["LD_LIBRARY_PATH"]] if env.get("LD_LIBRARY_PATH") else []))
        env["GI_TYPELIB_PATH"] = os.pathsep.join(
            [typelibs] + ([env["GI_TYPELIB_PATH"]] if env.get("GI_TYPELIB_PATH") else []))
        env["GST_PLUGIN_PATH"] = plugins
        env["GST_PLUGIN_SYSTEM_PATH_1_0"] = plugins
        env["GST_REGISTRY"] = os.path.join(
            tempfile.gettempdir(), f"epicpen-gst-registry-{os.getuid()}.bin")
    return env


def _system_env() -> dict:
    """os.environ SEM as injeções do AppImage/python-standalone.

    Ao rodar o python3 do sistema (com o GStreamer do sistema), o LD_PRELOAD
    (libxcb-cursor empacotada) e o LD_LIBRARY_PATH (Qt/xcb empacotados) do
    AppImage contaminam o carregamento das libs e o pipewiresrc entrega ZERO
    frames (o handshake do portal completa, mas nenhum buffer desce). Medido:
    com o env limpo, 35 frames em 4s; com o env do AppImage, nenhum. Também
    remove PYTHON*/GST_*/GI_* que apontariam para o runtime empacotado.
    """
    env = dict(os.environ)
    for k in ("LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH",
              "PYTHONNOUSERSITE", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
              "GST_PLUGIN_PATH", "GST_PLUGIN_SYSTEM_PATH_1_0",
              "GI_TYPELIB_PATH", "GST_REGISTRY",
              # SPA/PIPEWIRE: se vazarem apontando para dir errado, a libpipewire
              # do sistema falha em carregar 'support.system' (pw_loop_new) e o
              # pipewiresrc não sobe. Deixa a libpipewire usar os defaults.
              "SPA_PLUGIN_DIR", "PIPEWIRE_MODULE_DIR",
              "PIPEWIRE_CONFIG_DIR", "PIPEWIRE_CONFIG_NAME",
              "PIPEWIRE_CONFIG_PREFIX"):
        env.pop(k, None)
    return env


def _interp_candidates() -> list[tuple[str, dict]]:
    """Interpretadores a testar para o helper, em ordem de preferência.

    1) o próprio intérprete: em dev é a venv com system-site-packages (gi do
       sistema); no AppImage com bundle é o python empacotado + env do bundle.
    2) python3 do sistema (env sanitizado): salva o AppImage buildado FORA do
       CI Ubuntu, cujo bundle GStreamer não foi gerado — o gi/gstreamer do
       sistema entra no lugar (é o que os pacotes do erro instalam). O env
       limpo é obrigatório: ver _system_env.
    """
    cands: list[tuple[str, dict]] = [(sys.executable, _bundle_env())]
    seen = {os.path.realpath(sys.executable)}
    sys_env = _system_env()
    for name in ("python3", "python"):
        p = shutil.which(name)
        for cand in (p, f"/usr/bin/{name}"):
            if cand and os.path.exists(cand) and os.path.realpath(cand) not in seen:
                seen.add(os.path.realpath(cand))
                cands.append((cand, dict(sys_env)))
    return cands


def _probe_interp(python: str, env: dict) -> bool:
    """True se este python consegue gi + Gst + pipewiresrc (helper --check)."""
    try:
        r = subprocess.run(
            [python, _HELPER, "--check"], env=env, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except Exception:
        return False


def gstreamer_available() -> bool:
    """True se algum intérprete consegue consumir o stream do portal.

    Sondado uma vez por sessão via helper --check (isola o gi do processo Qt).
    Testa o intérprete próprio e, em fallback, o python3 do sistema — o
    primeiro que passar vira o interpretador do helper (_helper_interp).
    Nenhum passando desabilita o portal (recorder cai para QScreenCapture).
    """
    global _gst_ok, _helper_interp
    if _gst_ok is not None:
        return _gst_ok
    for python, env in _interp_candidates():
        if _probe_interp(python, env):
            _helper_interp = (python, env)
            _gst_ok = True
            return _gst_ok
    _gst_ok = False
    return _gst_ok


def available() -> bool:
    return gstreamer_available()


def helper_env() -> dict:
    """Ambiente do interpretador escolhido para o helper."""
    if _helper_interp is None:
        gstreamer_available()
    return _helper_interp[1] if _helper_interp else _bundle_env()


def _is_wayland() -> bool:
    """True numa sessão Wayland (WAYLAND_DISPLAY ou XDG_SESSION_TYPE)."""
    return bool(os.environ.get("WAYLAND_DISPLAY")) or \
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def needs_portal(source: str, show_cursor: bool) -> bool:
    """Quando usar o portal ScreenCast em vez do QScreenCapture silencioso.

    Janela isolada e ocultar cursor: só o portal faz. Monitor COM cursor:
    o QScreenCapture do Qt embute o cursor no X11, mas NÃO no Wayland (medido
    no KDE) — por isso no Wayland desviamos pro portal (cursor_mode EMBEDDED),
    que é a única forma de o cursor aparecer na gravação de tela inteira.
    O restore_token persistido evita repetir o seletor após a 1ª vez.
    """
    if source in ("window", "region") or not show_cursor:
        return True
    return _is_wayland()


def helper_cmd(source: str, show_cursor: bool,
               restore_token: str | None, persist: bool = False) -> list[str]:
    if _helper_interp is None:
        gstreamer_available()
    python = _helper_interp[0] if _helper_interp else sys.executable
    opts: dict = {"source": source, "cursor": bool(show_cursor),
                  "persist": bool(persist)}
    if restore_token:
        opts["restore_token"] = restore_token
    return [python, _HELPER, json.dumps(opts)]

"""
Define keepAbove=true para as janelas deste processo via KWin DBus scripting.
Funciona no KDE Plasma 6 Wayland sem roubar foco de teclado.

Comportamento confirmado do KWin 6:
  - Objeto em /Scripting/Script{N}, interface org.kde.kwin.Script
  - Após run(), KWin remove o objeto E o script de m_scripts automaticamente
  - Portanto: loadScript+run() a cada chamada (sem cache de _script_id)
  - Sem cache, o ID é sempre estável (mesma posição em m_scripts)
"""
import os
import subprocess
import tempfile
import threading

_PLUGIN = "epicpen-keepabove"
_JS = (
    "(function(){{"
    "var pid={pid};"
    "var w=workspace.windows();"
    "for(var i=0;i<w.length;i++){{"
    "if(w[i].pid===pid)w[i].keepAbove=true;"
    "}}"
    "}})();"
)

_lock = threading.Lock()
_script_path: str | None = None
_available: bool | None = None  # None=não verificado, True=disponível, False=ausente


def _is_available() -> bool:
    global _available
    if _available is None:
        import shutil
        _available = shutil.which("qdbus-qt6") is not None
        if not _available:
            print("[keepabove] qdbus-qt6 não encontrado — keepAbove KWin desativado", flush=True)
    return _available


def _qdbus(*args, timeout=5):
    try:
        r = subprocess.run(
            ["qdbus-qt6", "org.kde.KWin"] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
        out = r.stdout.strip()
        # qdbus-qt6 imprime erros no stdout (não stderr)
        return r.returncode, out, r.stderr.strip() or out
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return -1, "", str(e)


def _run(pid: int):
    global _script_path

    with _lock:
        # Cria o arquivo JS uma única vez
        if _script_path is None:
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False)
            f.write(_JS.format(pid=pid))
            f.close()
            _script_path = f.name

        # Carrega o script (KWin remove-o de m_scripts após run(), então cada
        # chamada cria uma nova instância com o mesmo ID estável)
        rc, sid, err = _qdbus("/Scripting", "loadScript", _script_path, _PLUGIN)
        if rc != 0 or not sid.lstrip("-").isdigit() or int(sid) < 0:
            print(f"[keepabove] loadScript failed: {err!r}", flush=True)
            return

        # Executa: após run(), o objeto /Scripting/Script{sid} é removido pelo KWin
        rc, out, err = _qdbus(f"/Scripting/Script{sid}", "run")
        if rc != 0:
            print(f"[keepabove] Script{sid}.run() failed: {out!r}", flush=True)


def set_keepabove():
    """Set keepAbove=true para todas as janelas do processo via KWin scripting."""
    if not _is_available():
        return
    threading.Thread(target=_run, args=(os.getpid(),), daemon=True).start()

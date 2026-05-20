"""
Define keepAbove=true para as janelas deste processo via KWin DBus scripting.
Funciona no KDE Plasma 6 Wayland sem roubar foco de teclado.

Correções vs. tentativa anterior:
  - objeto em /Scripting/Script{N}, não /{N}
  - interface org.kde.kwin.Script, não org.kde.kwin.Scripting
  - binário qdbus-qt6, não qdbus
  - pluginName fixo evita recarregar o script a cada chamada
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
_script_id: str | None = None
_script_path: str | None = None


def _qdbus(*args, timeout=5):
    try:
        r = subprocess.run(
            ["qdbus-qt6", "org.kde.KWin"] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return -1, "", str(e)


def _run(pid: int):
    global _script_id, _script_path

    with _lock:
        # Cria o arquivo JS uma única vez por processo
        if _script_path is None:
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False)
            f.write(_JS.format(pid=pid))
            f.close()
            _script_path = f.name

        # Carrega o script uma única vez (pluginName deduplica no KWin)
        if _script_id is None:
            rc, sid, err = _qdbus("/Scripting", "loadScript", _script_path, _PLUGIN)
            if rc != 0 or not sid.lstrip("-").isdigit():
                print(f"[keepabove] loadScript failed: {err or sid!r}", flush=True)
                return
            _script_id = sid

        # Executa: /Scripting/Script{N} → org.kde.kwin.Script.run()
        rc, _, err = _qdbus(f"/Scripting/Script{_script_id}", "run")
        if rc != 0:
            print(f"[keepabove] Script{_script_id}.run() failed: {err!r}", flush=True)


def set_keepabove():
    """Set keepAbove=true para todas as janelas do processo via KWin scripting."""
    threading.Thread(target=_run, args=(os.getpid(),), daemon=True).start()

"""Ambiente limpo para subprocessos que executam binários do sistema.

Dentro do AppImage, o AppRun exporta LD_LIBRARY_PATH/LD_PRELOAD apontando
para as libs bundladas (Qt6, xcb...). Ferramentas do sistema lançadas como
subprocesso (spectacle, gnome-screenshot, ffmpeg, pactl, xdg-open) herdam
essas variáveis e carregam libs incompatíveis — o spectacle crasha e o
screenshot caía sempre no fallback de portal, por exemplo.

host_env() devolve uma cópia do ambiente sem as entradas que apontam para
dentro do AppDir. Fora do AppImage é o ambiente intacto.
"""
import os

_BUNDLE_VARS = (
    "LD_LIBRARY_PATH", "LD_PRELOAD",
    "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
    "PYTHONPATH", "PYTHONHOME",
)


def host_env() -> dict:
    env = dict(os.environ)
    appdir = env.get("APPDIR", "")
    if not appdir:
        return env
    for var in _BUNDLE_VARS:
        val = env.get(var)
        if val is None:
            continue
        kept = [p for p in val.split(":") if p and not p.startswith(appdir)]
        if kept:
            env[var] = ":".join(kept)
        else:
            del env[var]
    return env

import json
from pathlib import Path

_CONFIG_DIR  = Path.home() / ".config" / "epicpen"
_CONFIG_FILE = _CONFIG_DIR / "config.json"

DEFAULTS: dict = {
    "tool": "pen",
    "color": "#FF0000",
    "size": 3,
    "toolbar_pos": {"x": 20, "y": 150},
    "magnifier_zoom": 3,
    "whiteboard": False,
}


def load() -> dict:
    if _CONFIG_FILE.exists():
        try:
            saved = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            return {**DEFAULTS, **saved}
        except Exception:
            pass
    return dict(DEFAULTS)


def save(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

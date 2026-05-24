import json
import sys
import importlib
from pathlib import Path
import pytest


def _load_config_module(tmp_path):
    """Recarrega config apontando para tmp_path."""
    import config as mod
    mod._CONFIG_DIR  = tmp_path / "epicpen"
    mod._CONFIG_FILE = mod._CONFIG_DIR / "config.json"
    return mod


def test_load_returns_defaults_when_no_file(tmp_path):
    mod = _load_config_module(tmp_path)
    result = mod.load()
    assert result["tool"] == "pen"
    assert result["color"] == "#FF0000"
    assert result["size"] == 3


def test_save_creates_directory_and_file(tmp_path):
    mod = _load_config_module(tmp_path)
    mod.save({"tool": "highlighter", "color": "#00FF00", "size": 5})
    assert mod._CONFIG_FILE.exists()


def test_load_restores_saved_values(tmp_path):
    mod = _load_config_module(tmp_path)
    data = {"tool": "eraser", "color": "#0000FF", "size": 10}
    mod.save(data)
    restored = mod.load()
    assert restored["tool"] == "eraser"
    assert restored["color"] == "#0000FF"
    assert restored["size"] == 10


def test_load_merges_missing_keys_with_defaults(tmp_path):
    mod = _load_config_module(tmp_path)
    mod._CONFIG_DIR.mkdir(parents=True)
    mod._CONFIG_FILE.write_text(json.dumps({"tool": "laser"}))
    result = mod.load()
    assert result["tool"] == "laser"
    assert result["size"] == 3          # vem dos defaults
    assert result["color"] == "#FF0000"


def test_load_returns_defaults_on_corrupt_json(tmp_path):
    mod = _load_config_module(tmp_path)
    mod._CONFIG_DIR.mkdir(parents=True)
    mod._CONFIG_FILE.write_text("{ broken json !!!")
    result = mod.load()
    assert result["tool"] == "pen"

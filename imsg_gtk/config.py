"""XDG-compliant config management for imsg-gtk."""

import json
import os
from pathlib import Path

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 5100,
    "token": "",
    "pinned_chat_ids": [],
}


def config_dir() -> Path:
    path = Path.home() / ".config" / "imsg-gtk"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    path = _config_path()
    if not path.exists():
        return dict(DEFAULTS)
    with open(path) as f:
        data = json.load(f)
    merged = dict(DEFAULTS)
    merged.update(data)
    return merged


def save(data: dict) -> None:
    path = _config_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(path)
    os.chmod(path, 0o600)

"""Settings for the tenk CLI: config.yaml (or TENK_CONFIG), with .env loaded for API keys.

The file is read as a plain nested dict, one section per stage (llm, labeling, chunking,
retrieval, generation); each experiment keeps its own copy (experiments/v*/config.yaml).
"""

import os
from pathlib import Path

import yaml

CONFIG_PATH = Path("config.yaml")


def load(path: Path | None = None) -> dict:
    """The settings in `path`, else $TENK_CONFIG, else ./config.yaml."""
    load_dotenv()
    path = path or Path(os.environ.get("TENK_CONFIG") or CONFIG_PATH)
    return yaml.safe_load(path.read_text())


def load_dotenv(path: Path = Path(".env")) -> None:
    """Set variables from KEY=VALUE lines, without overriding the real environment."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

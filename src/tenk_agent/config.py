"""Settings: config.yaml, then environment variables (and .env) on top for the model choices."""

import os
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

CONFIG_PATH = Path("config.yaml")

# config.yaml key -> (Settings field, environment variable or None)
KEYS = {
    "data_dir": ("data_dir", "TENK_DATA_DIR"),
    "ingest.chunk_chars": ("chunk_chars", None),
    "ingest.table_chars": ("table_chars", None),
    "ingest.overlap_chars": ("overlap_chars", None),
    "retrieve.embedder": ("embedder", "TENK_EMBEDDER"),
    "retrieve.reranker": ("reranker", "TENK_RERANKER"),
    "retrieve.mode": ("retrieval", "TENK_RETRIEVAL"),
    "retrieve.candidates": ("candidates", None),
    "retrieve.slot_search": ("slot_search", None),
    "retrieve.device": ("device", "TENK_LOCAL_DEVICE"),
    "model.name": ("model", "TENK_MODEL"),
    "model.base_url": ("base_url", "OPENAI_BASE_URL"),
    "model.thinking": ("thinking", "TENK_OPENAI_THINKING"),
    "model.gpu_hourly_usd": ("gpu_hourly_usd", "TENK_GPU_HOURLY_USD"),
    "answer.k": ("ask_k", None),
    "agent.max_tool_calls": ("max_tool_calls", "TENK_MAX_TOOL_CALLS"),
    "agent.section_chars": ("section_chars", None),
    "agent.structured_data": ("structured_data", "TENK_STRUCTURED_DATA"),
    "verify.enabled": ("verify", None),
    "verify.repair": ("repair", None),
    "verify.repair_calls": ("repair_calls", None),
    "eval.judge_model": ("judge_model", "TENK_JUDGE_MODEL"),
}
BOOLS = {"slot_search", "thinking", "structured_data", "verify", "repair"}


@dataclass
class Settings:
    """Code defaults match config.yaml; `Settings.load()` reads the file and the environment."""

    data_dir: Path = Path("data")
    chunk_chars: int = 2400
    table_chars: int = 7200
    overlap_chars: int = 600
    embedder: str = "local:Qwen/Qwen3-Embedding-0.6B"
    reranker: str = "local:BAAI/bge-reranker-v2-m3"
    retrieval: str = "dense"
    candidates: int = 40
    slot_search: bool = True
    device: str | None = None
    model: str = "anthropic:claude-sonnet-5"
    base_url: str = "http://localhost:8001/v1"
    thinking: bool | None = None
    gpu_hourly_usd: float = 0.86
    ask_k: int = 8
    max_tool_calls: int = 15
    section_chars: int = 24000
    structured_data: bool = False
    verify: bool = True
    repair: bool = True
    repair_calls: int = 4
    judge_model: str = "anthropic:claude-haiku-4-5"

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Settings":
        load_dotenv()
        settings = cls()
        data = yaml.safe_load(path.read_text()) if path.exists() else {}
        defaults = {f.name: f.default for f in fields(cls)}
        for key, (name, env) in KEYS.items():
            value = _lookup(data, key)
            if env and os.environ.get(env):
                value = os.environ[env]
            if value is not None:
                setattr(settings, name, _convert(value, name, defaults[name]))
        return settings

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tenk.db"


def _lookup(data: dict, dotted: str):
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _convert(value, name: str, default):
    if name in BOOLS:
        return (
            value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}
        )
    if isinstance(default, Path):
        return Path(value)
    if default is None:
        return value
    return type(default)(value)


def load_dotenv(path: Path = Path(".env")) -> None:
    """Set variables from KEY=VALUE lines, without overriding the real environment."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

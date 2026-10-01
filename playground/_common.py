"""Shared helpers for the stage playgrounds: settings, report file, opening it in Cursor."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)  # config.yaml, data/ and eval/ are relative to the repo root
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")  # no model-loading progress bars
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
sys.path.insert(0, str(REPO / "src"))

from tenk_agent.config import Settings  # noqa: E402


def settings() -> Settings:
    return Settings.load()


def write_report(stage: str, tag: str, text: str) -> Path:
    """Save the report as output/<stage>/<tag>.md and open it as a Cursor tab if possible."""
    path = REPO / "output" / stage / f"{tag}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"\n-> {path.relative_to(REPO)}")
    if "VSCODE_IPC_HOOK_CLI" in os.environ:  # set in Cursor / VS Code terminals
        subprocess.run(["cursor", "--reuse-window", str(path)], check=False)
    return path


def table(headers: list[str], rows: list[list]) -> str:
    """A Markdown table."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(lines)


def preview(text: str, chars: int = 160) -> str:
    return " ".join(text.split())[:chars]

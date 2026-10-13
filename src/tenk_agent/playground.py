"""Shared helpers for the version playgrounds (versions/v*/): settings, report file, Cursor tab.

Importing it sets the quiet-logging variables and moves to the repo root, where config.yaml,
data/ and eval/ live. Models load lazily, so import order among tenk_agent modules is free.
"""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # src/tenk_agent/playground.py
os.chdir(REPO)  # config.yaml, data/ and eval/ are relative to the repo root
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")  # no model-loading progress bars
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
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

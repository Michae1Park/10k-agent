"""Your own data-source credentials: credentials.yaml (or TENK_CREDENTIALS), never committed.

  tenk setup          type in your credentials; writes credentials.yaml (mode 600)

Each one is read from its environment variable first (a .env file also works), then from the
file. A credential that is missing, blank or still the example placeholder raises
MissingCredential naming it and how to set it, so nothing runs with someone else's key or none.
credentials.example.yaml is the committed template; credentials.yaml and .env are gitignored.
"""

import getpass
import os
import sys
from pathlib import Path

import yaml

from tenk_agent.config import load_dotenv

PATH = Path("credentials.yaml")
PLACEHOLDER = "REPLACE_ME"

# name → (environment variable, what it is, where to get one)
CREDENTIALS = {
    "sec_user_agent": ("SEC_USER_AGENT", "SEC EDGAR User-Agent: your name and email, "
                       "e.g. 'Jane Doe jane@example.com'",
                       "https://www.sec.gov/os/accessing-edgar-data"),
    "tiingo_api_key": ("TIINGO_API_KEY", "Tiingo API key (daily prices; internal use only)",
                       "https://www.tiingo.com/account/api/token"),
    "fred_api_key": ("FRED_API_KEY", "FRED API key (macro series)",
                     "https://fred.stlouisfed.org/docs/api/api_key.html"),
}


class MissingCredential(RuntimeError):
    pass


def path() -> Path:
    return Path(os.environ.get("TENK_CREDENTIALS") or PATH)


def _usable(value) -> bool:
    return isinstance(value, str) and value.strip() not in ("", PLACEHOLDER)


def get(name: str) -> str:
    """The credential's value, or MissingCredential with how to set it."""
    env, what, where = CREDENTIALS[name]
    load_dotenv()
    value = os.environ.get(env)
    if not _usable(value):
        file = path()
        value = (yaml.safe_load(file.read_text()) or {}).get(name) if file.exists() else None
    if not _usable(value):
        raise MissingCredential(
            f"Missing credential: {what}.\n"
            f"  Run `tenk setup` to type in your own credentials (saved to {path()}, not "
            f"committed),\n  or set {env}. Get one at {where}")
    if name == "sec_user_agent" and "@" not in value:
        raise MissingCredential(f"{what}: {value!r} has no email address; run `tenk setup`")
    return value.strip()


def setup(file: Path | None = None) -> int:
    """Prompt for every credential and write the file (owner read/write only)."""
    file = file or path()
    current = (yaml.safe_load(file.read_text()) or {}) if file.exists() else {}
    if not sys.stdin.isatty():
        print("tenk setup needs a terminal to type your credentials in", file=sys.stderr)
        return 2
    print(f"Your own credentials, saved to {file} (gitignored; never commit it).")
    out = {}
    for name, (_, what, where) in CREDENTIALS.items():
        print(f"\n{what}\n  get one: {where}")
        keep = " (Enter keeps the saved one)" if _usable(current.get(name)) else ""
        ask = input if name == "sec_user_agent" else getpass.getpass
        while True:
            value = ask(f"  {name}{keep}: ").strip() or current.get(name, "")
            if not _usable(value):
                print("  required")
            elif name == "sec_user_agent" and "@" not in value:
                print("  SEC needs a contact: your name and email")
            else:
                break
        out[name] = value
    file.write_text(yaml.safe_dump(out, sort_keys=False))
    file.chmod(0o600)
    print(f"\nSaved {file}.")
    return 0

"""Credentials (missing → explicit error) and outside forecast inputs (research/outside.py)."""

import pytest

from tenk_agent import cli, credentials
from tenk_agent.research import outside


@pytest.fixture
def no_creds(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env, no credentials.yaml
    for env, _, _ in credentials.CREDENTIALS.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("TENK_CREDENTIALS", str(tmp_path / "credentials.yaml"))
    return tmp_path


def test_missing_credential_names_it_and_setup(no_creds):
    with pytest.raises(credentials.MissingCredential, match="(?s)Tiingo.*tenk setup"):
        credentials.get("tiingo_api_key")


def test_placeholder_is_missing(no_creds):
    (no_creds / "credentials.yaml").write_text("fred_api_key: REPLACE_ME\n")
    with pytest.raises(credentials.MissingCredential, match="FRED"):
        credentials.get("fred_api_key")


def test_file_and_env(no_creds, monkeypatch):
    (no_creds / "credentials.yaml").write_text("sec_user_agent: A b@c.com\nfred_api_key: k1\n")
    assert credentials.get("sec_user_agent") == "A b@c.com"
    monkeypatch.setenv("FRED_API_KEY", "k2")
    assert credentials.get("fred_api_key") == "k2"


def test_sec_agent_needs_email(no_creds, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "no email")
    with pytest.raises(credentials.MissingCredential, match="email"):
        credentials.get("sec_user_agent")


def test_cli_fetch_without_credentials_errors(no_creds, capsys):
    (no_creds / "config.yaml").write_text("data_dir: data\n")
    assert cli.main(["fetch"]) == 2
    assert "Missing credential" in capsys.readouterr().err


def test_example_file_has_only_placeholders():
    from pathlib import Path

    import yaml

    example = yaml.safe_load((Path(__file__).parents[1] / "credentials.example.yaml").read_text())
    assert set(example) == set(credentials.CREDENTIALS)
    assert all(v == credentials.PLACEHOLDER for v in example.values())


def test_outside_fit_and_window():
    a, b, r2 = outside.fit([0.0, 0.1, 0.2], [0.05, 0.10, 0.15])
    assert (round(a, 6), round(b, 6), round(r2, 6)) == (0.05, 0.5, 1.0)
    yoy = outside.monthly_yoy([(f"2024-{m:02d}-01", 100.0) for m in range(1, 13)]
                              + [(f"2025-{m:02d}-01", 110.0) for m in range(1, 7)])
    assert outside.window(yoy, outside.month_index("2025-06")) == pytest.approx(0.1)
    assert outside.window(yoy, outside.month_index("2025-07")) is None
    assert outside.quarter_end("2025Q3") == outside.month_index("2025-09")

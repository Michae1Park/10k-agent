"""Workbook DCF and Model (prior) tabs (research/export/report.py, spec §2.3)."""

import shutil
import subprocess
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from tenk_agent.research import valuation
from tenk_agent.research.export import report
from test_valuation import Model, V


def _valuation(wacc=0.09, g=0.03):
    d = valuation.dcf(Model(50.0), "2026-09-01", wacc, g, 0.2, V)
    return d, {"as_of": "2026-09-01", "net_debt": 200.0, "shares": 10.0,
               "dcf_grid": {f"{w:.4f}|{x:.4f}": 1.0 for w in (0.08, 0.09) for x in (0.025, 0.03)},
               "dcf_base": {"wacc": wacc, "terminal_growth": g, "flows": d["flows"],
                            "terminal_t": d["terminal_t"], "last_annual": d["last_annual"]}}


@pytest.mark.skipif(shutil.which("soffice") is None, reason="needs LibreOffice")
def test_dcf_tab_formulas_recalculate_to_python(tmp_path: Path):
    d, v = _valuation()
    book = Workbook()
    report.dcf_tab(book, v)
    path = tmp_path / "dcf.xlsx"
    book.save(path)
    out = tmp_path / "out"
    subprocess.run([shutil.which("soffice"), "--headless", "--calc", "--convert-to", "xlsx",
                    "--outdir", str(out), str(path)], check=True, capture_output=True, timeout=300)
    ws = load_workbook(out / "dcf.xlsx", data_only=True)["DCF"]
    got = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(1, ws.max_row + 1)}
    assert got["EV (USD millions)"] == pytest.approx(d["ev"], rel=1e-9)
    assert got["terminal value share of EV"] == pytest.approx(d["terminal_share"], rel=1e-9)
    assert got["DCF value per share (USD)"] == pytest.approx((d["ev"] - 200.0) / 10.0, rel=1e-9)


def test_prior_tab_without_parent_says_initial_draft():
    book = Workbook()
    report.prior_tab(book, None, None)
    assert book["Model (prior)"]["A1"].value == "initial draft (no prior run)"


def test_prior_tab_lists_parent_values():
    book = Workbook()
    prior = {"as_of": "2026-05-01", "columns": ["2026Q1", "FY2026"],
             "lines": [{"name": "revenue", "label": "Revenue", "unit": "USD millions"}],
             "cells": {"revenue|2026Q1": {"value": 10.0}, "revenue|FY2026": {"value": 45.0}}}
    report.prior_tab(book, prior, "TEST:v1")
    rows = list(book["Model (prior)"].values)
    assert rows[1] == ("line", "unit", "2026Q1", "FY2026")
    assert rows[2] == ("Revenue", "USD millions", 10.0, 45.0)

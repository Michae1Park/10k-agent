"""Table rows as records: each data row with its group header, column labels and fiscal years (V7).

A 10-K table loses its meaning row by row: "| Revenue | $106,265 | $87,464 |" says nothing about
which segment or which years. A record carries both:

    Intelligent Cloud › Revenue: FY2025 = $106,265; FY2024 = $87,464; FY2023 = $72,944

- Header rows are the rows before the first data row (a label plus a number that isn't a year).
- A one-cell row after the header ("| Intelligent Cloud |", "| Reality Labs: |") starts a group.
- Columns come from the last header row with several cells; values are matched to them in
  order. A column's fiscal year is the year in its label ("2025", "June 30, 2025", "Jan 26,
  2025"): every corpus filer names a fiscal year after the calendar year it ends in. A
  one-year header above segment columns (Apple's transposed segment note) applies to all.
"""

import re
from dataclasses import dataclass, field

YEAR = re.compile(r"\b(20[0-3]\d)\b")
DATE_OR_YEAR = re.compile(
    r"^(?:[A-Za-z]{3,9}\.?\s+\d{1,2},\s*)?20[0-3]\d$|^20[0-3]\d$", re.IGNORECASE
)
HEADER_WORDS = re.compile(
    r"year|ended|as of|months|in (millions|thousands|billions)|except", re.IGNORECASE
)
NUMBER = re.compile(r"\d")


@dataclass
class Column:
    label: str
    fiscal_year: int | None  # None for change columns ("2024 vs. 2023", "%")


@dataclass
class Row:
    group: str  # "" outside any group
    label: str
    cells: list[tuple[Column, str]] = field(default_factory=list)

    def text(self) -> str:
        """One line, e.g. 'Intelligent Cloud › Revenue: FY2025 = $106,265; FY2024 = $87,464'."""
        name = f"{self.group} › {self.label}" if self.group else self.label
        values = "; ".join(f"{_column_name(col)} = {value}" for col, value in self.cells)
        return f"{name}: {values}"

    def value_for(self, fiscal_year: int) -> str | None:
        return next((v for col, v in self.cells if col.fiscal_year == fiscal_year), None)


def table_rows(rows: list[list[str]], caption: str = "") -> list[Row]:
    """Records for the data rows of a parsed table (rows as cell lists, label first).

    `caption` ("...as of December 31, 2025 (in millions)") dates a table whose columns aren't dated.
    """
    first_data = next((i for i, row in enumerate(rows) if _is_data_row(row)), len(rows))
    header, body = rows[:first_data], rows[first_data:]
    columns = _columns(header)
    caption_years = set(YEAR.findall(caption))
    if len(caption_years) == 1 and not any(c.fiscal_year for c in columns):
        year = int(caption_years.pop())
        columns = [Column(c.label, year) for c in columns]
    records, group = [], _leading_group(header)
    for row in body:
        if len(row) == 1:
            group = row[0].rstrip(":").strip()
            continue
        label, values = row[0], row[1:]
        if group and _closes(label, group):
            # "Total net sales" after "Net sales:" ends the group; so does a repeat of its name.
            records.append(Row("", label, _match(columns, values)))
            group = ""
            continue
        if not NUMBER.search(" ".join(values)) and not any(v in ("—", "-", "$—") for v in values):
            # A second header inside the table (e.g. per-year blocks): columns restart here.
            columns = _columns([row]) or columns
            continue
        records.append(Row(group, label, _match(columns, values)))
    return records


def header_rows(rows: list[list[str]]) -> list[list[str]]:
    first_data = next((i for i, row in enumerate(rows) if _is_data_row(row)), len(rows))
    return rows[:first_data]


def _is_data_row(row: list[str]) -> bool:
    if len(row) < 2 or not row[0]:
        return False
    return any(
        NUMBER.search(YEAR.sub("", cell)) and not DATE_OR_YEAR.match(cell) for cell in row[1:]
    )


def _leading_group(header: list[list[str]]) -> str:
    """A one-cell row after the column labels ("| Americas: |") is the first data row's group."""
    last_multi = max((i for i, row in enumerate(header) if len(row) > 1), default=-1)
    for row in header[last_multi + 1 :][::-1]:
        if len(row) == 1 and not YEAR.search(row[0]) and not HEADER_WORDS.search(row[0]):
            return row[0].rstrip(":").strip()
    return ""


def _closes(label: str, group: str) -> bool:
    label = label.lower()
    return label.startswith("total") or label.rstrip(":").strip() == group.lower()


def _columns(header: list[list[str]]) -> list[Column]:
    """Column labels from the last multi-cell header row; a one-year row above sets the year."""
    context_year = None
    for row in header:
        if len(row) == 1 and (years := YEAR.findall(row[0])) and len(set(years)) == 1:
            context_year = int(years[0])
    multi = [row for row in header if len(row) > 1]
    if not multi:
        return [Column(str(context_year), context_year)] if context_year else []
    dated = [row for row in multi if any(YEAR.search(cell) for cell in row)]
    cells = (dated or multi)[-1]
    # "Year Ended June 30, | 2025 | 2024" — the first cell labels the row, not a column.
    if not YEAR.search(cells[0]) and any(YEAR.search(c) for c in cells[1:]):
        cells = cells[1:]
    elif HEADER_WORDS.search(cells[0]) and not DATE_OR_YEAR.match(cells[0]):
        cells = cells[1:]
    columns = []
    for cell in cells:
        years = set(YEAR.findall(cell))
        year = int(years.pop()) if len(years) == 1 and DATE_OR_YEAR.match(cell.strip()) else None
        columns.append(
            Column(cell, year if year is not None else context_year if not years else None)
        )
    return columns


def _match(columns: list[Column], values: list[str]) -> list[tuple[Column, str]]:
    """Values to columns in order; extra values (change columns) get unlabeled columns."""
    if len(columns) > len(values):  # spanning header: values sit under the last columns
        columns = columns[len(columns) - len(values) :]
    if not columns:
        columns = [Column("", None)]
    pairs = []
    for i, value in enumerate(values):
        column = columns[i] if i < len(columns) else Column("", None)
        pairs.append((column, value))
    return pairs


def _column_name(column: Column) -> str:
    if column.fiscal_year is not None and DATE_OR_YEAR.match(column.label.strip()):
        return f"FY{column.fiscal_year}"
    if column.fiscal_year is not None:
        return f"{column.label} FY{column.fiscal_year}"
    if not re.search(r"[A-Za-z]", column.label) or YEAR.search(column.label):
        return f"change {column.label}".strip()  # "$%", "2024 vs. 2023"
    return column.label

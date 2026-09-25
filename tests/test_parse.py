from tenk_agent.parse import parse_filing


def html(body: str) -> str:
    return f"<html><body>{body}</body></html>"


CONTENTS = """
<table>
  <tr><td>Item 1.</td><td>Business</td><td>1</td></tr>
  <tr><td>Item 7.</td><td>Management's Discussion</td><td>20</td></tr>
  <tr><td>Item 8.</td><td>Financial Statements</td><td>30</td></tr>
</table>
"""


def test_sections_split_at_item_headings_and_skip_table_of_contents():
    sections = parse_filing(
        html(
            CONTENTS
            + """
        <div>Item 1. Business</div><div>We make things.</div>
        <div>Item 7. Management's Discussion and Analysis</div><div>Sales grew.</div>
    """
        )
    )
    assert [(s.item, s.title) for s in sections] == [
        ("1", "Business"),
        ("7", "Management's Discussion and Analysis"),
    ]
    assert sections[0].blocks[0].text == "We make things."


def test_headings_in_single_row_tables_are_recognized():
    sections = parse_filing(
        html("<table><tr><td>Item 1A. Risk Factors</td></tr></table><p>Risky.</p>")
    )
    assert [s.item for s in sections] == ["1A"]


def test_cross_references_and_page_headers_are_not_headings():
    sections = parse_filing(
        html("""
        <div>Item 1. Business</div>
        <div>Item 5.02 of Form 8-K</div>
        <div>Item 1B, 1C</div>
        <div>Still business.</div>
    """)
    )
    assert [s.item for s in sections] == ["1"]
    assert [b.text for b in sections[0].blocks] == ["Item 5.02 of Form 8-K", "Still business."]


def test_split_table_cells_are_merged():
    sections = parse_filing(
        html("""
        <div>Item 8. Financial Statements</div>
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Net income</td><td>$</td><td>1,234</td><td>(</td><td> 56 </td><td>)</td></tr>
          <tr><td>Change</td><td>(3</td><td>)%</td><td>4</td><td>%</td></tr>
        </table>
    """)
    )
    table = sections[0].blocks[0]
    assert table.kind == "table"
    assert table.rows[1:] == [["Net income", "$1,234", "(56)"], ["Change", "(3)%", "4%"]]


def test_financial_statements_move_into_a_pointer_only_item_8():
    sections = parse_filing(
        html("""
        <div>Item 8. Financial Statements and Supplementary Data</div>
        <div>The information required by this Item is included in Item 15.</div>
        <div>Item 9A. Controls and Procedures</div>
        <div>Report of Independent Registered Public Accounting Firm</div>
        <div>Opinion on Internal Control Over Financial Reporting</div>
        <div>Item 15. Exhibits and Financial Statement Schedules</div>
        <div>Report of Independent Registered Public Accounting Firm</div>
        <div>Opinion on the Consolidated Financial Statements</div>
        <table><tr><td>Revenue</td><td>100</td></tr><tr><td>Net income</td><td>10</td></tr></table>
    """)
    )
    by_item = {s.item: s for s in sections}
    assert any(b.kind == "table" for b in by_item["8"].blocks)
    assert "Opinion on Internal Control Over Financial Reporting" in [
        b.text for b in by_item["9A"].blocks
    ]
    assert by_item["15"].blocks == []

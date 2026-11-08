# TODO: make the build match the spec and design doc (temporary)

The spec and design doc agree (2026-11-08); the build changes to match them. Delete this file once done.

- [ ] Note says "initial draft (no prior version)"; both docs say "(no prior run)" (`note/sections.py:99`, `:135`, `:206`)
- [ ] Section 1 "Rating / PT change" vs the parent: `rating_change` is always `None` (`pipeline.py:441`); fill it from the parent's rating and PT
- [ ] Section 9 prints "Open issues: N" counting every exception; the docs say "Exceptions: N (by severity)" (`note/sections.py`)
- [ ] Section 4 prints "4.3 Integrity checks"; the docs number it 4.2 (`note/sections.py`)
- [ ] Remove the unused `forecast.variance_flag` key from `research.yaml` (the variance flag was dropped)
- [ ] Regenerate `EXAMPLE_UBER_Q2_2026_MODEL_CHANGE_WORKBOOK.xlsx` to the revised spec example (no 2028E; Revisions tab; Q3 anchored at the midpoints; scenario table with multiple and DCF per share)
- [ ] Bring `V4_INTERFACES.md` and `stages/V4_*.md` in line with the two docs (dropped: variance flag, consensus column, X-04, initiation, held-out set, section 10 repair, 3.1 driver table)

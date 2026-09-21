"""Generate normalized 2025 FE CAP records from the checked-in official PDF.

The source PDF is the MHT-CET 2025-26 CAP Round I cutoff list.  It contains
the complete first-year engineering record set; do not substitute DSE CAP
tables when refreshing this file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterator

import pandas as pd
import pypdfium2 as pdfium

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_loader import DataLoader, _clean_and_resolve_city


COLLEGE_RE = re.compile(r"^(\d{5})\s+-\s+(.+?)\s*$")
COURSE_RE = re.compile(r"^(\d{10})\s+-\s+(.+?)\s*$")
STAGE_VALUES_RE = re.compile(r"^(I|II|III|IV)\s+(.+?)\s*$")
PERCENT_RE = re.compile(r"\(([-+]?\d+(?:\.\d+)?)\)")
RANK_RE = re.compile(r"\d+")
SEAT_CODE_RE = re.compile(r"^[A-Z0-9-]+$")
STAGE_LABELS = {"I", "II", "III", "IV"}


def _is_seat_code_token(token: str) -> bool:
    """Return True for CAP seat-code labels, excluding stage/rank text."""
    return (
        bool(SEAT_CODE_RE.fullmatch(token))
        and token not in STAGE_LABELS
        and not token.isdigit()
        and any(ch.isalpha() for ch in token)
    )


def _records_from_page(text: str) -> Iterator[dict[str, str]]:
    """Yield row-level CAP records from one text-extracted cutoff-list page."""
    college_code = ""
    college_name = ""
    course_code = ""
    course_name = ""
    seat_codes: list[str] = []
    collecting_header = False
    pending: tuple[str, list[str], list[str]] | None = None

    def flush_pending() -> Iterator[dict[str, str]]:
        nonlocal pending
        if pending is None:
            return
        round_name, ranks, percentiles = pending
        pending = None
        # PDF extraction omits blank table cells. A record is reliable only
        # when rank and percentile columns line up with the seat-code header.
        if len(ranks) != len(percentiles) or len(percentiles) > len(seat_codes):
            return
        for seat_code, rank, percentile in zip(seat_codes, ranks, percentiles):
            yield {
                "College Code": college_code,
                "College Name": college_name,
                "Choice Code": course_code,
                "Course Name": course_name,
                "Stage": round_name,
                "Category": seat_code,
                "Cutoff Rank": rank,
                "Percentile": percentile,
            }

    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue

        college = COLLEGE_RE.match(line)
        if college:
            yield from flush_pending()
            college_code, college_name = college.groups()
            course_code = ""
            course_name = ""
            seat_codes = []
            collecting_header = False
            continue

        course = COURSE_RE.match(line)
        if course:
            yield from flush_pending()
            course_code, course_name = course.groups()
            # A choice code always embeds its institute code. Ignore malformed
            # headings rather than attributing records to the prior college.
            if course_code[:5] != college_code:
                course_code = ""
                course_name = ""
            seat_codes = []
            collecting_header = False
            continue

        stage_values = STAGE_VALUES_RE.match(line)
        if stage_values and course_code and seat_codes:
            yield from flush_pending()
            round_name, rank_text = stage_values.groups()
            pending = (round_name, RANK_RE.findall(rank_text), [])
            collecting_header = False
            continue

        # PDFium preserves the source table text but emits the visual
        # ``Stage`` label separately.  The following all-uppercase line is
        # the CAP seat-code header for the next stage block.
        header_tokens = line.split()
        if header_tokens and all(_is_seat_code_token(token) for token in header_tokens):
            yield from flush_pending()
            seat_codes = [*seat_codes, *header_tokens] if collecting_header else header_tokens
            collecting_header = True
            continue

        if pending is not None and line.isdigit():
            round_name, ranks, percentiles = pending
            # Page numbers are emitted as standalone digits after a complete
            # table. Do not mistake them for another cutoff rank.
            if len(ranks) < len(seat_codes):
                pending = (round_name, [*ranks, line], percentiles)
            continue

        if pending is not None and line.startswith("("):
            round_name, ranks, _ = pending
            pending = (round_name, ranks, [*pending[2], *PERCENT_RE.findall(line)])

    yield from flush_pending()


def _historical_city_by_code() -> dict[str, str]:
    """Build a non-pathway-specific location fallback from existing raw data."""
    result: dict[str, str] = {}
    data_dir = Path(__file__).resolve().parent
    sources = (
        (data_dir / "converted_college_data_final.csv", "College Code", "City"),
        (data_dir / "fe_2024.csv", "College Name", "City"),
    )
    for path, code_column, city_column in sources:
        if not path.is_file():
            continue
        source = pd.read_csv(path, dtype=str, usecols=[code_column, city_column])
        for code, city in zip(source[code_column], source[city_column]):
            match = re.search(r"\d{1,5}", str(code))
            resolved = _clean_and_resolve_city(city)
            if match and resolved:
                result.setdefault(str(int(match.group())).zfill(5), resolved)
    return result


def parse_cutoff_pdf(pdf_path: Path) -> pd.DataFrame:
    records: list[dict[str, str]] = []
    pdf = pdfium.PdfDocument(pdf_path)
    for page in pdf:
        text = page.get_textpage().get_text_range()
        records.extend(_records_from_page(text))
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        raise ValueError(f"No FE CAP records parsed from {pdf_path}")
    loader = DataLoader()
    historical_city_by_code = _historical_city_by_code()
    # Prefer a location stated in the official institute heading over old
    # metadata keyed by DTE code. Some institutes are represented in multiple
    # admission pathways and stale metadata can otherwise move Navi Mumbai
    # records into Mumbai or discard valid colleges entirely.
    frame["City"] = [
        _clean_and_resolve_city("", name)
        or historical_city_by_code.get(str(code).zfill(5), "")
        or loader._college_city_by_code.get(str(code).zfill(5), "")
        for code, name in zip(frame["College Code"], frame["College Name"])
    ]
    return frame.drop_duplicates().reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).with_name("fe2025.mahacet.org.pdf"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("fe_2025.csv"))
    args = parser.parse_args()

    frame = parse_cutoff_pdf(args.source)
    frame.to_csv(args.output, index=False)
    print(f"Wrote {len(frame):,} FE CAP records for {frame['College Code'].nunique():,} colleges to {args.output}")


if __name__ == "__main__":
    main()

"""Generate normalized 2024 FE CAP records from the official CET Cell PDF.

The source PDF is the MHT-CET 2024-25 CAP Round I cutoff list. It contains
the complete first-year engineering record set; do not substitute DSE CAP
tables when refreshing this file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data.generate_fe_2025 import parse_cutoff_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).with_name("fe2024.mahacet.org.pdf"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("fe_2024.csv"))
    args = parser.parse_args()

    frame = parse_cutoff_pdf(args.source)
    frame.to_csv(args.output, index=False)
    print(f"Wrote {len(frame):,} FE CAP records for {frame['College Code'].nunique():,} colleges to {args.output}")


if __name__ == "__main__":
    main()

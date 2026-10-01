"""Record a human decision on the current scene image without changing it."""
import argparse
import json
import sys
from pathlib import Path

import production_review


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--status", required=True, choices=("accepted", "rejected", "review-needed"))
    parser.add_argument("--note", default="")
    parser.add_argument("--reviewer")
    parser.add_argument("--output-hash", help="Expected current output hash; rejects stale decisions")
    args = parser.parse_args(argv)
    try:
        record = production_review.record_review(args.project, args.scene, args.status, args.note,
                    reviewer=args.reviewer, expected_output_hash=args.output_hash)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

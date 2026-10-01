"""Invoke when a WhatsApp announcement arrives to record its current UTC receipt.

Earlier receipts remain unchanged; corrections append a new receipt observed now.
"""
import argparse
import json
import sys
from pathlib import Path

import production_events


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--event", required=True, choices=("start", "finish"))
    parser.add_argument("--correction-of", help="Prior receipt ID; append a correction observed now")
    args = parser.parse_args(argv)
    try:
        record = production_events.record_announcement(args.project, args.run_id, args.event,
                                                       correction_of=args.correction_of)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

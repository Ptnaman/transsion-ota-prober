#!/usr/bin/env python3
"""Verify that every selected historical OTA title was saved after notification."""

from pathlib import Path
import sys

selected_path = Path(".backfill_recent_titles.txt")
processed_path = Path("processed_updates.txt")

if not selected_path.exists():
    print("Missing .backfill_recent_titles.txt", file=sys.stderr)
    raise SystemExit(2)
if not processed_path.exists():
    print("Missing processed_updates.txt", file=sys.stderr)
    raise SystemExit(2)

selected = {line.strip() for line in selected_path.read_text(encoding="utf-8").splitlines() if line.strip()}
processed = {line.strip() for line in processed_path.read_text(encoding="utf-8").splitlines() if line.strip()}
missing = sorted(selected - processed)

if missing:
    print(f"Backfill incomplete: {len(missing)} selected OTA title(s) were not saved after notification:", file=sys.stderr)
    for title in missing:
        print(f"  - {title}", file=sys.stderr)
    raise SystemExit(1)

print(f"Backfill verified: all {len(selected)} selected OTA title(s) are processed.")

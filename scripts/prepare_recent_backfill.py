#!/usr/bin/env python3
"""Prepare a one-time historical OTA backfill without changing tracker configs.

The normal tracker deduplicates by processed_updates.txt.  For a historical
backfill we temporarily hide that file, run a dry scan to learn each currently
available OTA's metadata/build date, then remove only the recent titles from the
processed set.  A following normal checkota run can therefore notify those
recent titles using the tracker's existing Telegram formatter/notifier.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
TITLE_RE = re.compile(r"New OTA update found:\s*(.+?)\s*$")
BUILD_DATE_RE = re.compile(r"Build date:\s*(\d{4}-\d{2}-\d{2})\s+[0-9:]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-date", required=True, help="Inclusive YYYY-MM-DD cutoff")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=1200)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cutoff = dt.date.fromisoformat(args.since_date)
    except ValueError:
        print(f"Invalid --since-date: {args.since_date!r}; expected YYYY-MM-DD", file=sys.stderr)
        return 2

    processed = Path("processed_updates.txt")
    backup = Path(".processed_updates.backfill-backup")
    discovery_log = Path("backfill-discovery.log")
    selected_file = Path(".backfill_recent_titles.txt")

    if not processed.exists():
        print("processed_updates.txt is missing; run the baseline workflow first.", file=sys.stderr)
        return 2

    original_text = processed.read_text(encoding="utf-8")
    original_titles = [line.strip() for line in original_text.splitlines() if line.strip()]
    original_set = set(original_titles)

    if backup.exists():
        backup.unlink()
    processed.replace(backup)

    cmd = [
        "checkota",
        "-d",
        "configs/",
        "--jobs",
        str(args.jobs),
        "--timeout",
        str(args.timeout),
        "--dry-run",
    ]

    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
        combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        discovery_log.write_text(combined, encoding="utf-8")
    finally:
        # Dry-run should not create this file, but never let a temporary file
        # replace the user's committed baseline state.
        if processed.exists():
            processed.unlink()
        backup.replace(processed)

    if proc.returncode != 0:
        print(f"Discovery scan failed with exit code {proc.returncode}.", file=sys.stderr)
        print(f"See {discovery_log} for details.", file=sys.stderr)
        return proc.returncode or 1

    current_title: str | None = None
    dated_titles: list[tuple[str, dt.date]] = []

    for raw_line in combined.splitlines():
        line = ANSI_RE.sub("", raw_line)
        title_match = TITLE_RE.search(line)
        if title_match:
            current_title = title_match.group(1).strip()
            continue

        date_match = BUILD_DATE_RE.search(line)
        if date_match and current_title:
            try:
                build_day = dt.date.fromisoformat(date_match.group(1))
            except ValueError:
                current_title = None
                continue
            dated_titles.append((current_title, build_day))
            current_title = None

    selected: list[str] = []
    seen: set[str] = set()
    for title, build_day in dated_titles:
        if build_day >= cutoff and title not in seen:
            selected.append(title)
            seen.add(title)

    if not selected:
        print(f"No OTA builds found on or after {cutoff.isoformat()}.")
        selected_file.write_text("", encoding="utf-8")
        return 0

    # Keep every baseline title except the historical titles we want to post.
    # Any genuinely new OTA that appeared after baseline remains absent and can
    # still be sent normally during the following live run.
    filtered = [title for title in original_titles if title not in seen]
    processed.write_text("".join(f"{title}\n" for title in filtered), encoding="utf-8")
    selected_file.write_text("".join(f"{title}\n" for title in selected), encoding="utf-8")

    already_baselined = sum(1 for title in selected if title in original_set)
    print(f"Selected {len(selected)} OTA title(s) with build date >= {cutoff.isoformat()}.")
    print(f"Temporarily removed {already_baselined} selected title(s) from the baseline.")
    print("Selected titles:")
    for title in selected:
        print(f"  - {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

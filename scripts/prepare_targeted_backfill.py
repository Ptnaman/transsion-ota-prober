#!/usr/bin/env python3
"""Prepare a targeted historical OTA backfill for selected device families.

This performs a sequential dry-run over all configs so Device -> OTA -> Build date
log lines stay associated. It then removes only matching recent OTA titles from
processed_updates.txt and writes the matching device codenames for a following
Telegram notification pass.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
DEVICE_RE = re.compile(r"Device:\s*(.+?)\s+\(([^()]+)\)\s*$")
TITLE_RE = re.compile(r"New OTA update found:\s*(.+?)\s*$")
BUILD_DATE_RE = re.compile(r"Build date:\s*(\d{4}-\d{2}-\d{2})\s+[0-9:]+")

# Requested families: Infinix GT 20, all TECNO POVA (including POVA 7),
# and TECNO CAMON 30 series.
TARGET_RE = re.compile(r"(?:\bGT\s*20\b|\bPOVA\b|\bCAMON\s*30\b)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-date", required=True, help="Inclusive YYYY-MM-DD cutoff")
    parser.add_argument("--timeout", type=int, default=1800)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cutoff = dt.date.fromisoformat(args.since_date)
    except ValueError:
        print(f"Invalid --since-date: {args.since_date!r}; expected YYYY-MM-DD", file=sys.stderr)
        return 2

    processed = Path("processed_updates.txt")
    backup = Path(".processed_updates.target-backup")
    discovery_log = Path("targeted-backfill-discovery.log")
    selected_file = Path(".backfill_recent_titles.txt")
    devices_file = Path(".backfill_target_devices.txt")

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
        "1",
        "--timeout",
        str(args.timeout),
        "--dry-run",
    ]

    proc: subprocess.CompletedProcess[str]
    combined = ""
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
        combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        discovery_log.write_text(combined, encoding="utf-8")
    finally:
        if processed.exists():
            processed.unlink()
        backup.replace(processed)

    if proc.returncode != 0:
        print(f"Discovery scan failed with exit code {proc.returncode}.", file=sys.stderr)
        print(f"See {discovery_log} for details.", file=sys.stderr)
        return proc.returncode or 1

    current_device_name: str | None = None
    current_device_code: str | None = None
    pending_title: tuple[str, str, str] | None = None
    dated: list[tuple[str, str, str, dt.date]] = []

    for raw_line in combined.splitlines():
        line = ANSI_RE.sub("", raw_line)

        device_match = DEVICE_RE.search(line)
        if device_match:
            current_device_name = device_match.group(1).strip()
            current_device_code = device_match.group(2).strip()
            pending_title = None
            continue

        title_match = TITLE_RE.search(line)
        if title_match and current_device_name and current_device_code:
            pending_title = (
                title_match.group(1).strip(),
                current_device_name,
                current_device_code,
            )
            continue

        date_match = BUILD_DATE_RE.search(line)
        if date_match and pending_title:
            try:
                build_day = dt.date.fromisoformat(date_match.group(1))
            except ValueError:
                pending_title = None
                continue
            title, device_name, device_code = pending_title
            dated.append((title, device_name, device_code, build_day))
            pending_title = None

    selected_titles: list[str] = []
    selected_devices: list[str] = []
    selected_rows: list[tuple[str, str, str, dt.date]] = []
    seen_titles: set[str] = set()
    seen_devices: set[str] = set()

    for title, device_name, device_code, build_day in dated:
        if build_day < cutoff:
            continue
        if not TARGET_RE.search(device_name):
            continue
        if title not in seen_titles:
            selected_titles.append(title)
            selected_rows.append((title, device_name, device_code, build_day))
            seen_titles.add(title)
        if device_code not in seen_devices:
            selected_devices.append(device_code)
            seen_devices.add(device_code)

    filtered = [title for title in original_titles if title not in seen_titles]
    processed.write_text("".join(f"{title}\n" for title in filtered), encoding="utf-8")
    selected_file.write_text("".join(f"{title}\n" for title in selected_titles), encoding="utf-8")
    devices_file.write_text("".join(f"{device}\n" for device in selected_devices), encoding="utf-8")

    if not selected_titles:
        print(f"No matching GT 20 / POVA / CAMON 30 OTA builds found on or after {cutoff.isoformat()}.")
        return 0

    already_processed = sum(1 for title in selected_titles if title in original_set)
    print(f"Selected {len(selected_titles)} matching OTA title(s) with build date >= {cutoff.isoformat()}.")
    print(f"Target device config(s): {len(selected_devices)}")
    print(f"Temporarily removed {already_processed} already-processed title(s) so they can be pushed again.")
    print("Selected updates:")
    for title, device_name, device_code, build_day in selected_rows:
        print(f"  - {device_name} ({device_code}) | {build_day.isoformat()} | {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

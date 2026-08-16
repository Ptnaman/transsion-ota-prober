#!/usr/bin/env python3
"""Prepare a targeted historical OTA backfill for selected device families.

This performs a sequential dry-run over all configs so Device -> OTA -> Build date
log lines stay associated. It then removes only matching recent OTA titles from
processed_updates.txt and writes the matching config keys for a following
Telegram notification pass.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import yaml

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


def build_device_to_config_map() -> dict[str, str]:
    """Map YAML device values (e.g. TECNO-LJ8) to checkota config keys (e.g. LJ8)."""
    mapping: dict[str, str] = {}
    for path in sorted(Path("configs").glob("config-*.yml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
            continue
        device = str(data.get("device", "")).strip()
        if not device:
            continue
        config_key = path.stem.removeprefix("config-")
        mapping[device] = config_key
    return mapping


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

    device_to_config = build_device_to_config_map()
    if not device_to_config:
        print("Could not build device-to-config mapping from configs/.", file=sys.stderr)
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
    current_device_id: str | None = None
    pending_title: tuple[str, str, str] | None = None
    dated: list[tuple[str, str, str, dt.date]] = []

    for raw_line in combined.splitlines():
        line = ANSI_RE.sub("", raw_line)

        device_match = DEVICE_RE.search(line)
        if device_match:
            current_device_name = device_match.group(1).strip()
            current_device_id = device_match.group(2).strip()
            pending_title = None
            continue

        title_match = TITLE_RE.search(line)
        if title_match and current_device_name and current_device_id:
            pending_title = (
                title_match.group(1).strip(),
                current_device_name,
                current_device_id,
            )
            continue

        date_match = BUILD_DATE_RE.search(line)
        if date_match and pending_title:
            try:
                build_day = dt.date.fromisoformat(date_match.group(1))
            except ValueError:
                pending_title = None
                continue
            title, device_name, device_id = pending_title
            dated.append((title, device_name, device_id, build_day))
            pending_title = None

    selected_titles: list[str] = []
    selected_configs: list[str] = []
    selected_rows: list[tuple[str, str, str, str, dt.date]] = []
    seen_titles: set[str] = set()
    seen_configs: set[str] = set()

    unresolved: set[str] = set()

    for title, device_name, device_id, build_day in dated:
        if build_day < cutoff:
            continue
        if not TARGET_RE.search(device_name):
            continue

        config_key = device_to_config.get(device_id)
        if not config_key:
            unresolved.add(device_id)
            continue

        if title not in seen_titles:
            selected_titles.append(title)
            selected_rows.append((title, device_name, device_id, config_key, build_day))
            seen_titles.add(title)
        if config_key not in seen_configs:
            selected_configs.append(config_key)
            seen_configs.add(config_key)

    if unresolved:
        print("Warning: matching devices with no config mapping:", file=sys.stderr)
        for device_id in sorted(unresolved):
            print(f"  - {device_id}", file=sys.stderr)

    filtered = [title for title in original_titles if title not in seen_titles]
    processed.write_text("".join(f"{title}\n" for title in filtered), encoding="utf-8")
    selected_file.write_text("".join(f"{title}\n" for title in selected_titles), encoding="utf-8")
    devices_file.write_text("".join(f"{config}\n" for config in selected_configs), encoding="utf-8")

    if not selected_titles:
        print(f"No matching GT 20 / POVA / CAMON 30 OTA builds found on or after {cutoff.isoformat()}.")
        return 0

    already_processed = sum(1 for title in selected_titles if title in original_set)
    print(f"Selected {len(selected_titles)} matching OTA title(s) with build date >= {cutoff.isoformat()}.")
    print(f"Target config(s): {len(selected_configs)}")
    print(f"Temporarily removed {already_processed} already-processed title(s) so they can be pushed again.")
    print("Selected updates:")
    for title, device_name, device_id, config_key, build_day in selected_rows:
        print(
            f"  - {device_name} ({device_id}) -> config {config_key} | "
            f"{build_day.isoformat()} | {title}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

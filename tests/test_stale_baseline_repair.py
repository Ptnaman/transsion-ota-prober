"""Regression tests for stale OTA baselines that can hide later major updates."""

from argparse import Namespace
from pathlib import Path
from textwrap import dedent

from checkota.manager import Config
from checkota.models import VariantUpdate
from checkota.processor import apply_update_actions, collect_update_info
from checkota.runtime import RunContext


KNOWN_TITLE = "Tcard_X6853-15.0.3.127-OP001PF001AZ"
OLD_FP = (
    "Infinix/X6853-OP/Infinix-X6853:15/"
    "AP3A.240905.015.A2/180003:user/release-keys"
)
SAME_ANDROID_TARGET_FP = (
    "Infinix/X6853-OP/Infinix-X6853:15/"
    "AP3A.240905.015.A2/190127:user/release-keys"
)
ANDROID_16_TARGET_FP = (
    "Infinix/X6853-OP/Infinix-X6853:16/"
    "BP2A.250605.031.A3/301450013:user/release-keys"
)


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config-X6853.yml"
    path.write_text(
        dedent(
            """\
            oem: "Infinix"
            product: "X6853-OP"
            device: "Infinix-X6853"
            android_version: "15"
            build_tag: "AP3A.240905.015.A2"
            incremental: "180003"
            model: "Infinix NOTE 40 4G"
            """
        ),
        encoding="utf-8",
    )
    return path


def _ctx(tmp_path: Path, processed_titles: set[str] | None = None) -> RunContext:
    return RunContext(
        env={},
        processed_path=tmp_path / "processed_updates.txt",
        processed_titles=processed_titles or set(),
        dry_run=False,
    )


def _apply_args() -> Namespace:
    return Namespace(
        incremental=None,
        no_config=False,
        dry_run=False,
        force_notify=False,
        update_incremental=False,
    )


def test_known_ota_is_not_skipped_before_baseline_repair(tmp_path, monkeypatch):
    """A processed title must still reach OTA metadata so a stale config can heal."""
    path = _write_config(tmp_path)
    cfg = Config.from_yaml(path)[0]
    ctx = _ctx(tmp_path, {KNOWN_TITLE})

    class FakeChecker:
        def __init__(self, *args, **kwargs):
            pass

        def check(self, debug=False):
            return True, {
                "title": KNOWN_TITLE,
                "url": "https://example.invalid/ota.zip",
                "size": "123",
                "description": "Known OTA",
            }

    monkeypatch.setattr("checkota.processor.UpdateChecker", FakeChecker)
    monkeypatch.setattr(
        "checkota.processor.get_cached_ota_metadata",
        lambda ctx, url: {
            "fingerprint": ANDROID_16_TARGET_FP,
            "post_build_incremental": "301450013",
            "post_sdk_level": "36",
            "android_version": "16",
        },
    )

    args = Namespace(
        imei=None,
        debug=False,
        gen_fp=False,
        dry_run=False,
        fp=None,
        register_update=False,
        update_incremental=False,
        force_notify=False,
    )

    status, update = collect_update_info(ctx, cfg, path, args)

    assert status == 0
    assert update is not None
    assert update.is_new_update is False
    assert update.target_fp == ANDROID_16_TARGET_FP


def test_known_ota_repairs_baseline_without_duplicate_notification(
    tmp_path, monkeypatch
):
    """Known OTA target fingerprints update YAML but must not notify again."""
    path = _write_config(tmp_path)
    cfg = Config.from_yaml(path)[0]
    ctx = _ctx(tmp_path, {KNOWN_TITLE})

    update = VariantUpdate(
        cfg=cfg,
        config_path=path,
        variant_label=None,
        region_name=None,
        title=KNOWN_TITLE,
        url="https://example.invalid/ota.zip",
        size="123",
        desc="Known OTA",
        is_new_update=False,
        target_fp=SAME_ANDROID_TARGET_FP,
        target_incremental="190127",
        sdk_message="Android 15",
        data={"fingerprint": SAME_ANDROID_TARGET_FP},
    )

    def fail_if_notifier_created(*args, **kwargs):
        raise AssertionError("known baseline repair must not create a notifier")

    monkeypatch.setattr(
        "checkota.processor.create_notifier", fail_if_notifier_created
    )

    assert apply_update_actions(ctx, update, _apply_args()) == 0

    repaired = Config.from_yaml(path)[0]
    assert repaired.android_version == "15"
    assert repaired.incremental == "190127"
    assert update.cfg.incremental == "190127"


def test_new_same_android_tcard_advances_config(tmp_path, monkeypatch):
    """Tcard security/maintenance builds are valid baselines even without OS change."""
    path = _write_config(tmp_path)
    cfg = Config.from_yaml(path)[0]
    ctx = _ctx(tmp_path)

    update = VariantUpdate(
        cfg=cfg,
        config_path=path,
        variant_label=None,
        region_name=None,
        title=KNOWN_TITLE,
        url="https://example.invalid/ota.zip",
        size="123",
        desc="New OTA",
        is_new_update=True,
        target_fp=SAME_ANDROID_TARGET_FP,
        target_incremental="190127",
        sdk_message="Android 15",
        data={"fingerprint": SAME_ANDROID_TARGET_FP},
    )

    monkeypatch.setattr("checkota.processor.create_notifier", lambda *a, **k: None)

    assert apply_update_actions(ctx, update, _apply_args()) == 0
    repaired = Config.from_yaml(path)[0]
    assert repaired.incremental == "190127"

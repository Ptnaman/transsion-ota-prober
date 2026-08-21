import argparse
import signal

from checkota import cli, processor
from checkota.models import PendingNotification
from checkota.runtime import RunContext


def test_sweep_drains_successful_notifications_when_another_check_fails(
    monkeypatch, tmp_path
):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = config_dir / "config-X6873.yml"
    config_path.write_text(
        "oem: Infinix\nproduct: X6873-OP\ndevice: Infinix-X6873\n"
        "android_version: '16'\nbuild_tag: B1\nincremental: I1\n"
        'model: "Infinix GT 30 Pro"\n',
        encoding="utf-8",
    )

    ctx = RunContext(
        env={"bot_token": "t", "chat_id": "c", "telegraph_token": "p"},
        processed_path=tmp_path / "processed_updates.txt",
        processed_titles=set(),
        dry_run=False,
    )
    args = argparse.Namespace(
        fp=None,
        config=None,
        config_dir=config_dir,
        no_config=False,
        update_incremental=False,
        gen_fp=False,
        incremental=False,
        dry_run=False,
        skip_telegram=False,
        register_update=False,
        timeout=0.0,
        jobs=1,
        region=None,
        debug=False,
        imei=None,
        run_context=ctx,
    )

    parser = argparse.ArgumentParser()
    monkeypatch.setattr(parser, "parse_args", lambda: args)
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    monkeypatch.setattr(cli, "_validate_args", lambda p, a: None)
    monkeypatch.setattr(
        cli, "create_run_context", lambda dry_run, pool_size=10, **kw: ctx
    )
    monkeypatch.setattr(cli, "install_interrupt_handler", lambda c: signal.SIG_DFL)
    monkeypatch.setattr(cli, "start_watchdog", lambda c, t: None)
    monkeypatch.setattr(cli, "_collect_config_paths", lambda p, a: [config_path])

    def fake_process_config(path, local_args):
        ctx.pending_notifications.append(
            PendingNotification(
                msg="<b>successful OTA</b>",
                device_title="X6873 - OTA",
                title="X6873-OTA",
                is_new_update=True,
            )
        )
        # Simulate an unrelated config/network failure in the same sweep.
        return 1

    sent = []

    class StubNotifier:
        def send(self, msg, truncate_desc=True, device_title=None):
            sent.append(device_title)
            return True

    monkeypatch.setattr(cli, "process_config", fake_process_config)
    monkeypatch.setattr(processor, "create_notifier", lambda c, a: StubNotifier())
    monkeypatch.setattr(processor, "SWEEP_TELEGRAM_DELAY", 0)

    rc = cli.main()

    assert rc == 1
    assert sent == ["X6873 - OTA"]
    assert ctx.pending_notifications == []

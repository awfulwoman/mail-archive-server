from __future__ import annotations
import time
from pathlib import Path
from starlette.testclient import TestClient
from mail_archive_server.main import bootstrap
from tests.conftest import install_fake_mbsync


def _make_folder(root: Path, *parts: str) -> Path:
    folder = root.joinpath(*parts)
    (folder / "cur").mkdir(parents=True)
    (folder / "new").mkdir(parents=True)
    (folder / "tmp").mkdir(parents=True)
    return folder


def test_bootstrap_indexes_at_startup(tmp_path):
    maildir = tmp_path / "maildir"
    inbox = _make_folder(maildir / "personal", "INBOX")
    (inbox / "cur" / "1.uniq:2,S").write_bytes(
        b"From: a@b.com\r\nSubject: Startup test\r\nDate: Tue, 01 Sep 2026 09:15:04 +0000\r\n\r\nbody"
    )

    app = bootstrap({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_DB_PATH": str(tmp_path / "index.db"),
        "MAIL_ARCHIVE_TOKENS__gateway__SECRET": "s1",
    })
    client = TestClient(app)

    resp = client.get("/messages", headers={"Authorization": "Bearer s1"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["messages"][0]["subject"] == "Startup test"


def test_bootstrap_run_scheduler_forwards_to_create_app(tmp_path):
    maildir = tmp_path / "maildir"
    maildir.mkdir()
    mbsync_bin = install_fake_mbsync(tmp_path)
    (tmp_path / "maildir_path.txt").write_text(str(maildir))

    app = bootstrap({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_DB_PATH": str(tmp_path / "index.db"),
        "MAIL_ARCHIVE_TOKENS__gateway__SECRET": "s1",
        "MAIL_ARCHIVE_MBSYNC_BIN": str(mbsync_bin),
        "MAIL_ARCHIVE_MBSYNCRC_PATH": str(tmp_path / "mbsyncrc"),
        "MAIL_ARCHIVE_SYNC_INTERVAL_SECONDS": "3600",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__HOST": "imap.example.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__USERNAME": "u",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__PASSWORD": "p",
    }, run_scheduler=True)

    with TestClient(app) as client:
        deadline = time.monotonic() + 3
        total = 0
        while time.monotonic() < deadline:
            total = client.get("/messages", headers={"Authorization": "Bearer s1"}).json()["total"]
            if total == 1:
                break
            time.sleep(0.05)
        assert total == 1


def test_bootstrap_run_scheduler_defaults_to_false(tmp_path):
    maildir = tmp_path / "maildir"
    maildir.mkdir()
    mbsync_bin = install_fake_mbsync(tmp_path)
    (tmp_path / "maildir_path.txt").write_text(str(maildir))
    mbsyncrc_path = tmp_path / "mbsyncrc"

    app = bootstrap({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_DB_PATH": str(tmp_path / "index.db"),
        "MAIL_ARCHIVE_TOKENS__gateway__SECRET": "s1",
        "MAIL_ARCHIVE_MBSYNC_BIN": str(mbsync_bin),
        "MAIL_ARCHIVE_MBSYNCRC_PATH": str(mbsyncrc_path),
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__HOST": "imap.example.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__USERNAME": "u",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__PASSWORD": "p",
    })

    with TestClient(app):
        time.sleep(0.3)
    assert not mbsyncrc_path.exists()


def test_bootstrap_uses_configured_host_and_port(tmp_path):
    (tmp_path / "maildir").mkdir()
    app = bootstrap({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(tmp_path / "maildir"),
        "MAIL_ARCHIVE_DB_PATH": str(tmp_path / "index.db"),
        "MAIL_ARCHIVE_TOKENS__gateway__SECRET": "s1",
        "MAIL_ARCHIVE_HOST": "127.0.0.1",
        "MAIL_ARCHIVE_PORT": "9999",
    })
    assert app.state.config.host == "127.0.0.1"
    assert app.state.config.port == 9999

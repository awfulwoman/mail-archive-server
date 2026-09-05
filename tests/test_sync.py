from __future__ import annotations
from pathlib import Path
from mail_archive_server.config import IMAPAccountConfig
from mail_archive_server.sync import generate_mbsyncrc, sync_all, write_mbsyncrc
from tests.conftest import install_fake_mbsync as _install_fake_mbsync


def _account(name: str, **overrides) -> IMAPAccountConfig:
    base = dict(name=name, host="imap.example.com", port=993, username="u", password="p", patterns="*")
    base.update(overrides)
    return IMAPAccountConfig(**base)


def test_generate_mbsyncrc_single_account():
    content = generate_mbsyncrc(
        [_account("personal", host="imap.example.com", username="c@example.com", password="hunter2")],
        maildir_path=Path("/data/mail"),
    )
    assert "IMAPAccount personal" in content
    assert "Host imap.example.com" in content
    assert "User c@example.com" in content
    assert "Pass hunter2" in content
    assert "Path /data/mail/personal/" in content
    assert "Inbox /data/mail/personal/INBOX" in content
    assert "SyncState /data/mail/.mbsync/personal/" in content
    assert "Channel personal" in content
    assert "Expunge None" in content
    assert "Patterns *" in content


def test_generate_mbsyncrc_multiple_accounts_each_own_syncstate():
    content = generate_mbsyncrc(
        [_account("personal"), _account("work", patterns="* ![Gmail]/All Mail")],
        maildir_path=Path("/data/mail"),
    )
    assert "SyncState /data/mail/.mbsync/personal/" in content
    assert "SyncState /data/mail/.mbsync/work/" in content
    assert "Patterns * ![Gmail]/All Mail" in content


def test_generate_mbsyncrc_empty_accounts():
    content = generate_mbsyncrc([], maildir_path=Path("/data/mail"))
    assert "IMAPAccount" not in content


def test_write_mbsyncrc_sets_restrictive_permissions(tmp_path):
    dest = tmp_path / "mbsyncrc"
    write_mbsyncrc(dest, "secret content")
    assert dest.read_text() == "secret content"
    mode = dest.stat().st_mode & 0o777
    assert mode == 0o600


def test_write_mbsyncrc_creates_parent_dir(tmp_path):
    dest = tmp_path / "nested" / "dir" / "mbsyncrc"
    write_mbsyncrc(dest, "x")
    assert dest.read_text() == "x"


def test_sync_all_reports_per_account_success(tmp_path):
    mbsync_bin = _install_fake_mbsync(tmp_path)
    mbsyncrc = tmp_path / "mbsyncrc"
    mbsyncrc.write_text("fake config")

    results = sync_all([_account("personal")], mbsync_bin=str(mbsync_bin), mbsyncrc_path=mbsyncrc)
    assert results["personal"].ok is True
    assert results["personal"].exit_code == 0


def test_sync_all_one_account_failing_does_not_stop_others(tmp_path):
    mbsync_bin = _install_fake_mbsync(tmp_path)
    mbsyncrc = tmp_path / "mbsyncrc"
    mbsyncrc.write_text("fake config")
    marker_dir = tmp_path / "fake_mbsync_markers"
    marker_dir.mkdir()
    (marker_dir / "work.fail").touch()

    results = sync_all(
        [_account("personal"), _account("work")], mbsync_bin=str(mbsync_bin), mbsyncrc_path=mbsyncrc
    )
    assert results["personal"].ok is True
    assert results["work"].ok is False
    assert results["work"].exit_code != 0
    assert "simulated failure" in results["work"].stderr

    invoked = (marker_dir / "invoked.log").read_text().splitlines()
    assert invoked == ["personal", "work"]


def test_sync_all_empty_accounts_returns_empty(tmp_path):
    mbsync_bin = _install_fake_mbsync(tmp_path)
    mbsyncrc = tmp_path / "mbsyncrc"
    mbsyncrc.write_text("fake config")
    assert sync_all([], mbsync_bin=str(mbsync_bin), mbsyncrc_path=mbsyncrc) == {}

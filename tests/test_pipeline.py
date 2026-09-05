from __future__ import annotations
from pathlib import Path
import pytest
from mail_archive_server.config import load_config
from mail_archive_server.pipeline import sync_and_reindex
from mail_archive_server.store import SearchFilters, get_meta, open_db, search
from tests.conftest import install_fake_mbsync


@pytest.fixture
def db():
    conn = open_db(Path(":memory:"))
    yield conn
    conn.close()


def _setup(tmp_path, accounts_env):
    maildir = tmp_path / "maildir"
    maildir.mkdir()
    bin_path = install_fake_mbsync(tmp_path)
    (tmp_path / "maildir_path.txt").write_text(str(maildir))

    env = {
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_TOKENS__gateway__SECRET": "s1",
        "MAIL_ARCHIVE_MBSYNC_BIN": str(bin_path),
        "MAIL_ARCHIVE_MBSYNCRC_PATH": str(tmp_path / "mbsyncrc"),
    }
    env.update(accounts_env)
    return load_config(env), maildir


def test_sync_and_reindex_writes_mbsyncrc_and_indexes_synced_mail(tmp_path, db):
    config, maildir = _setup(tmp_path, {
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__HOST": "imap.example.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__USERNAME": "u",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__PASSWORD": "p",
    })

    result = sync_and_reindex(db, config)

    assert config.mbsyncrc_path.exists()
    assert "IMAPAccount personal" in config.mbsyncrc_path.read_text()
    assert (config.mbsyncrc_path.stat().st_mode & 0o777) == 0o600

    assert result.sync["personal"].ok is True
    assert result.index["personal"].parsed == 1

    found = search(db, SearchFilters(accounts=["personal"]))
    assert found.total == 1
    assert found.messages[0]["subject"] == "Synced personal"


def test_sync_and_reindex_records_meta_per_account(tmp_path, db):
    config, maildir = _setup(tmp_path, {
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__HOST": "imap.example.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__USERNAME": "u",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__PASSWORD": "p",
    })

    sync_and_reindex(db, config)

    assert get_meta(db, "last_sync_attempt_at:personal") is not None
    assert get_meta(db, "last_sync_ok:personal") == "1"


def test_sync_and_reindex_one_account_failing_still_indexes_the_other(tmp_path, db):
    config, maildir = _setup(tmp_path, {
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__HOST": "imap.example.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__USERNAME": "u",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__PASSWORD": "p",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__work__HOST": "imap.work.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__work__USERNAME": "u2",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__work__PASSWORD": "p2",
    })
    marker_dir = tmp_path / "fake_mbsync_markers"
    marker_dir.mkdir()
    (marker_dir / "work.fail").touch()

    result = sync_and_reindex(db, config)

    assert result.sync["work"].ok is False
    assert result.sync["personal"].ok is True
    assert get_meta(db, "last_sync_ok:work") == "0"
    assert get_meta(db, "last_sync_ok:personal") == "1"

    found = search(db, SearchFilters(accounts=["personal", "work"]))
    assert found.total == 1
    assert found.messages[0]["account"] == "personal"


def test_sync_and_reindex_with_no_imap_accounts_only_reindexes(tmp_path, db):
    config, maildir = _setup(tmp_path, {})
    account_dir = maildir / "readonly" / "INBOX"
    (account_dir / "cur").mkdir(parents=True)
    (account_dir / "new").mkdir(parents=True)
    (account_dir / "tmp").mkdir(parents=True)
    (account_dir / "cur" / "1.uniq:2,S").write_bytes(
        b"From: a@b.com\r\nSubject: Pre-existing\r\n\r\nbody"
    )

    result = sync_and_reindex(db, config)

    assert not config.mbsyncrc_path.exists()
    assert result.sync == {}
    assert result.index["readonly"].parsed == 1

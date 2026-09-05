from __future__ import annotations
import os
from pathlib import Path
import pytest
from mail_archive_server.indexer import reindex, reindex_account
from mail_archive_server.store import (
    SearchFilters,
    compute_id,
    get_meta,
    open_db,
    search,
)


def _make_folder(root: Path, *parts: str) -> Path:
    folder = root.joinpath(*parts)
    (folder / "cur").mkdir(parents=True)
    (folder / "new").mkdir(parents=True)
    (folder / "tmp").mkdir(parents=True)
    return folder


def _write_message(path: Path, *, subject="Hello", from_addr="a@b.com",
                    date="Tue, 01 Sep 2026 09:15:04 +0000", body="body text",
                    mtime: float | None = None) -> None:
    raw = (
        f"From: {from_addr}\r\n"
        f"Subject: {subject}\r\n"
        + (f"Date: {date}\r\n" if date else "")
        + "\r\n"
        + body
    )
    path.write_bytes(raw.encode())
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def db():
    conn = open_db(Path(":memory:"))
    yield conn
    conn.close()


def test_reindex_account_parses_and_inserts_new_messages(tmp_path, db):
    root = tmp_path / "maildir"
    inbox = _make_folder(root / "personal", "INBOX")
    _write_message(inbox / "cur" / "1.uniq:2,S", subject="Invoice")

    stats = reindex_account(db, root / "personal", "personal")
    assert stats.parsed == 1
    assert stats.updated == 0
    assert stats.deleted == 0

    id_ = compute_id("personal", "INBOX", "1.uniq")
    result = search(db, SearchFilters(accounts=["personal"]))
    assert result.total == 1
    assert result.messages[0]["id"] == id_
    assert result.messages[0]["subject"] == "Invoice"
    assert result.messages[0]["seen"] is True


def test_reindex_account_does_not_reparse_unchanged_path(tmp_path, db):
    root = tmp_path / "maildir"
    inbox = _make_folder(root / "personal", "INBOX")
    f = inbox / "cur" / "1.uniq:2,S"
    _write_message(f, subject="Original")

    reindex_account(db, root / "personal", "personal")

    # mutate the file in place without changing its maildir name/flags — a real
    # mbsync run never does this, but it is the only way to prove from outside
    # that the "path unchanged -> skip, no reparse" branch really skips.
    _write_message(f, subject="Changed")

    stats = reindex_account(db, root / "personal", "personal")
    assert stats.parsed == 0
    assert stats.updated == 0

    result = search(db, SearchFilters(accounts=["personal"]))
    assert result.messages[0]["subject"] == "Original"


def test_reindex_account_flag_change_updates_without_reparsing(tmp_path, db):
    root = tmp_path / "maildir"
    inbox = _make_folder(root / "personal", "INBOX")
    f = inbox / "cur" / "1.uniq:2,S"
    _write_message(f, subject="Original")

    reindex_account(db, root / "personal", "personal")

    # rename to flip the Seen flag off (simulates mark-as-unread), and change the
    # on-disk content — content must NOT be reflected, proving this is a flag-only
    # update, not a reparse.
    f.rename(inbox / "cur" / "1.uniq:2,")
    _write_message(inbox / "cur" / "1.uniq:2,", subject="Changed", date=None)

    stats = reindex_account(db, root / "personal", "personal")
    assert stats.parsed == 0
    assert stats.updated == 1

    result = search(db, SearchFilters(accounts=["personal"]))
    assert result.messages[0]["subject"] == "Original"
    assert result.messages[0]["seen"] is False


def test_reindex_account_deletes_vanished_messages(tmp_path, db):
    root = tmp_path / "maildir"
    inbox = _make_folder(root / "personal", "INBOX")
    f = inbox / "cur" / "1.uniq:2,S"
    _write_message(f, subject="Going away")

    reindex_account(db, root / "personal", "personal")
    assert search(db, SearchFilters(accounts=["personal"])).total == 1

    f.unlink()
    stats = reindex_account(db, root / "personal", "personal")
    assert stats.deleted == 1
    assert search(db, SearchFilters(accounts=["personal"])).total == 0


def test_reindex_account_deletion_sweep_scoped_to_this_account(tmp_path, db):
    root = tmp_path / "maildir"
    personal_inbox = _make_folder(root / "personal", "INBOX")
    _write_message(personal_inbox / "cur" / "1.uniq:2,S", subject="Personal msg")
    work_inbox = _make_folder(root / "work", "INBOX")
    _write_message(work_inbox / "cur" / "1.uniq:2,S", subject="Work msg")

    reindex_account(db, root / "personal", "personal")
    reindex_account(db, root / "work", "work")
    assert search(db, SearchFilters(accounts=["personal", "work"])).total == 2

    # reindexing only "personal" must never touch "work" rows, even though
    # work's message wasn't seen in this walk.
    reindex_account(db, root / "personal", "personal")
    result = search(db, SearchFilters(accounts=["personal", "work"]))
    assert result.total == 2


def test_reindex_account_sets_last_indexed_at_meta(tmp_path, db):
    root = tmp_path / "maildir"
    inbox = _make_folder(root / "personal", "INBOX")
    _write_message(inbox / "cur" / "1.uniq:2,S")

    assert get_meta(db, "last_indexed_at:personal") is None
    reindex_account(db, root / "personal", "personal")
    assert get_meta(db, "last_indexed_at:personal") is not None


def test_date_falls_back_to_mtime_when_missing(tmp_path, db):
    root = tmp_path / "maildir"
    inbox = _make_folder(root / "personal", "INBOX")
    f = inbox / "cur" / "1.uniq:2,S"
    _write_message(f, date=None, mtime=1_767_225_600.0)  # 2026-01-01T00:00:00Z

    reindex_account(db, root / "personal", "personal")
    result = search(db, SearchFilters(accounts=["personal"]))
    assert result.messages[0]["date"] == "2026-01-01T00:00:00Z"


def test_reindex_discovers_all_accounts_by_default(tmp_path, db):
    root = tmp_path / "maildir"
    _make_folder(root / "personal", "INBOX")
    _write_message((root / "personal" / "INBOX" / "cur" / "1.uniq:2,S"))
    _make_folder(root / "work", "INBOX")
    _write_message((root / "work" / "INBOX" / "cur" / "1.uniq:2,S"))

    stats = reindex(db, root)
    assert set(stats.keys()) == {"personal", "work"}
    assert search(db, SearchFilters(accounts=["personal", "work"])).total == 2


def test_reindex_with_explicit_accounts_only_touches_those(tmp_path, db):
    root = tmp_path / "maildir"
    _make_folder(root / "personal", "INBOX")
    _write_message((root / "personal" / "INBOX" / "cur" / "1.uniq:2,S"))
    _make_folder(root / "work", "INBOX")
    _write_message((root / "work" / "INBOX" / "cur" / "1.uniq:2,S"))

    stats = reindex(db, root, accounts=["personal"])
    assert set(stats.keys()) == {"personal"}
    assert search(db, SearchFilters(accounts=["personal", "work"])).total == 1

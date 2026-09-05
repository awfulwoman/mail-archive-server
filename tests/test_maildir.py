from __future__ import annotations
from pathlib import Path
from mail_archive_server.maildir import discover_accounts, walk_account


def _make_folder(root: Path, *parts: str) -> Path:
    folder = root.joinpath(*parts)
    (folder / "cur").mkdir(parents=True)
    (folder / "new").mkdir(parents=True)
    (folder / "tmp").mkdir(parents=True)
    return folder


def _touch(path: Path, mtime: float = 1_700_000_000.0) -> None:
    path.write_bytes(b"From: a@b.com\r\n\r\nbody")
    import os
    os.utime(path, (mtime, mtime))


def test_discover_accounts_returns_sorted_first_level_dirs(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "personal").mkdir()
    (tmp_path / ".mbsync").mkdir()
    (tmp_path / "not_a_dir.txt").write_text("x")
    assert discover_accounts(tmp_path) == ["personal", "work"]


def test_discover_accounts_missing_root_returns_empty(tmp_path):
    assert discover_accounts(tmp_path / "nonexistent") == []


def test_walk_account_finds_top_level_folder(tmp_path):
    inbox = _make_folder(tmp_path / "personal", "INBOX")
    _touch(inbox / "cur" / "1000.uniq1:2,S")

    messages = list(walk_account(tmp_path / "personal", "personal"))
    assert len(messages) == 1
    m = messages[0]
    assert m.account == "personal"
    assert m.folder == "INBOX"
    assert m.maildir_name == "1000.uniq1"


def test_walk_account_finds_nested_folder_alongside_parent(tmp_path):
    archive = _make_folder(tmp_path / "personal", "Archive")
    archive_2019 = _make_folder(tmp_path / "personal", "Archive", "2019")
    _touch(archive / "cur" / "1.uniqA:2,S")
    _touch(archive_2019 / "cur" / "2.uniqB:2,S")

    messages = list(walk_account(tmp_path / "personal", "personal"))
    folders = sorted(m.folder for m in messages)
    assert folders == ["Archive", "Archive/2019"]


def test_new_messages_are_unseen_with_no_flags(tmp_path):
    inbox = _make_folder(tmp_path / "personal", "INBOX")
    _touch(inbox / "new" / "1000.uniq1")

    [m] = list(walk_account(tmp_path / "personal", "personal"))
    assert m.maildir_name == "1000.uniq1"
    assert m.flags == frozenset()
    assert m.seen is False
    assert m.deleted is False


def test_cur_flags_parsed_seen_and_deleted(tmp_path):
    inbox = _make_folder(tmp_path / "personal", "INBOX")
    _touch(inbox / "cur" / "a.uniq:2,S")
    _touch(inbox / "cur" / "b.uniq:2,ST")
    _touch(inbox / "cur" / "c.uniq:2,FRS")

    by_name = {m.maildir_name: m for m in walk_account(tmp_path / "personal", "personal")}
    assert by_name["a.uniq"].seen is True
    assert by_name["a.uniq"].deleted is False
    assert by_name["b.uniq"].seen is True
    assert by_name["b.uniq"].deleted is True
    assert by_name["c.uniq"].flags == frozenset({"F", "R", "S"})


def test_maildir_name_stable_across_flag_suffix(tmp_path):
    inbox = _make_folder(tmp_path / "personal", "INBOX")
    _touch(inbox / "cur" / "1000.uniq1:2,S")

    [m] = list(walk_account(tmp_path / "personal", "personal"))
    assert m.maildir_name == "1000.uniq1"
    assert ":2," not in m.maildir_name


def test_walk_account_skips_tmp_directory(tmp_path):
    inbox = _make_folder(tmp_path / "personal", "INBOX")
    _touch(inbox / "tmp" / "should-not-appear:2,S")

    messages = list(walk_account(tmp_path / "personal", "personal"))
    assert messages == []


def test_walk_account_skips_dotfiles(tmp_path):
    inbox = _make_folder(tmp_path / "personal", "INBOX")
    (inbox / "cur" / ".uidvalidity").write_text("123")
    _touch(inbox / "cur" / "real.uniq:2,S")

    messages = list(walk_account(tmp_path / "personal", "personal"))
    assert [m.maildir_name for m in messages] == ["real.uniq"]


def test_walk_account_records_path_mtime_size(tmp_path):
    inbox = _make_folder(tmp_path / "personal", "INBOX")
    f = inbox / "cur" / "real.uniq:2,S"
    _touch(f, mtime=1_700_000_123.0)

    [m] = list(walk_account(tmp_path / "personal", "personal"))
    assert m.path == f
    assert m.mtime == 1_700_000_123.0
    assert m.size == f.stat().st_size

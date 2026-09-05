from __future__ import annotations
from pathlib import Path
import pytest
from mail_archive_server.store import SearchFilters, compute_id, open_db


@pytest.fixture
def db():
    conn = open_db(Path(":memory:"))
    yield conn
    conn.close()


def _insert(conn, *, account="personal", folder="INBOX", maildir_name="m1",
            message_id="<a@b.com>", from_raw="Alice <alice@example.com>",
            from_addr="alice@example.com", to_raw="charlie@example.com",
            subject="Hello", date_utc="2026-09-01T09:15:04Z", size_bytes=1000,
            seen=True, deleted=False, has_attachment=False, attachments=None,
            body_preview="preview", body="full body text", indexed_at="2026-09-01T10:00:00Z"):
    from mail_archive_server.store import insert_message
    id_ = compute_id(account, folder, maildir_name)
    insert_message(
        conn,
        id=id_,
        account=account,
        folder=folder,
        path=f"/mail/{account}/{folder}/cur/{maildir_name}:2,S",
        maildir_name=maildir_name,
        message_id=message_id,
        from_raw=from_raw,
        from_addr=from_addr,
        to_raw=to_raw,
        cc_raw="",
        subject=subject,
        date_utc=date_utc,
        size_bytes=size_bytes,
        flags="S" if seen else "",
        seen=seen,
        deleted=deleted,
        has_attachment=has_attachment,
        attachments=attachments or [],
        body_preview=body_preview,
        body=body,
        indexed_at=indexed_at,
    )
    return id_


def test_compute_id_deterministic_and_scoped_to_account_folder_name():
    id1 = compute_id("personal", "INBOX", "m1")
    id2 = compute_id("personal", "INBOX", "m1")
    assert id1 == id2
    assert id1 != compute_id("work", "INBOX", "m1")
    assert id1 != compute_id("personal", "Archive", "m1")
    assert id1 != compute_id("personal", "INBOX", "m2")


def test_open_db_is_idempotent(tmp_path):
    path = tmp_path / "index.db"
    conn1 = open_db(path)
    conn1.close()
    conn2 = open_db(path)  # must not raise on existing schema
    conn2.close()


def test_insert_and_get_message(db):
    id_ = _insert(db)
    msg = get_message_helper(db, id_)
    assert msg["id"] == id_
    assert msg["account"] == "personal"
    assert msg["folder"] == "INBOX"
    assert msg["message_id"] == "<a@b.com>"
    assert msg["from"] == "Alice <alice@example.com>"
    assert msg["from_addr"] == "alice@example.com"
    assert msg["to"] == "charlie@example.com"
    assert msg["subject"] == "Hello"
    assert msg["date"] == "2026-09-01T09:15:04Z"
    assert msg["seen"] is True
    assert msg["deleted"] is False
    assert msg["size_bytes"] == 1000
    assert msg["has_attachment"] is False
    assert msg["attachments"] == []
    assert msg["body"] == "full body text"


def get_message_helper(conn, id_):
    from mail_archive_server.store import get_message
    return get_message(conn, id_)


def test_get_message_unknown_id_returns_none(db):
    from mail_archive_server.store import get_message
    assert get_message(db, "doesnotexist") is None


def test_attachments_round_trip_as_list_of_dicts(db):
    atts = [{"filename": "a.pdf", "content_type": "application/pdf",
             "size_bytes": 10, "content_disposition": "attachment"}]
    id_ = _insert(db, has_attachment=True, attachments=atts)
    msg = get_message_helper(db, id_)
    assert msg["has_attachment"] is True
    assert msg["attachments"] == atts


def test_update_flags_changes_path_seen_deleted_without_reparsing(db):
    from mail_archive_server.store import get_message, update_flags
    id_ = _insert(db, seen=False, deleted=False)
    before = get_message(db, id_)
    assert before["seen"] is False

    update_flags(db, id=id_, path="/new/path:2,ST", flags="ST", seen=True, deleted=True)
    after = get_message(db, id_)
    assert after["seen"] is True
    assert after["deleted"] is True
    assert after["subject"] == before["subject"]  # untouched — no reparse


def test_delete_ids_removes_message_and_from_fts(db):
    from mail_archive_server.store import delete_ids, get_message
    id_ = _insert(db, subject="Unique Findme Subject")
    assert get_message(db, id_) is not None

    delete_ids(db, [id_])
    assert get_message(db, id_) is None

    results = search(db, SearchFilters(accounts=["personal"], q="Findme"))
    assert results.total == 0


def test_ids_for_account_returns_id_to_path_map(db):
    from mail_archive_server.store import ids_for_account
    id1 = _insert(db, account="personal", folder="INBOX", maildir_name="m1")
    id2 = _insert(db, account="personal", folder="INBOX", maildir_name="m2")
    _insert(db, account="work", folder="INBOX", maildir_name="m1")  # different account

    mapping = ids_for_account(db, "personal")
    assert set(mapping.keys()) == {id1, id2}
    assert mapping[id1].endswith("m1:2,S")


def test_meta_set_and_get_roundtrip(db):
    from mail_archive_server.store import get_meta, set_meta
    assert get_meta(db, "last_indexed_at:personal") is None
    set_meta(db, "last_indexed_at:personal", "2026-09-01T10:00:00Z")
    assert get_meta(db, "last_indexed_at:personal") == "2026-09-01T10:00:00Z"
    set_meta(db, "last_indexed_at:personal", "2026-09-02T10:00:00Z")
    assert get_meta(db, "last_indexed_at:personal") == "2026-09-02T10:00:00Z"


def test_known_accounts_derived_from_meta_keys(db):
    from mail_archive_server.store import known_accounts, set_meta
    assert known_accounts(db) == set()
    set_meta(db, "last_indexed_at:personal", "2026-09-01T10:00:00Z")
    set_meta(db, "last_indexed_at:work", "2026-09-01T10:00:00Z")
    assert known_accounts(db) == {"personal", "work"}


def search(conn, filters):
    from mail_archive_server.store import search as _search
    return _search(conn, filters)


def test_search_filters_by_account(db):
    _insert(db, account="personal", folder="INBOX", maildir_name="p1")
    _insert(db, account="work", folder="INBOX", maildir_name="w1")

    result = search(db, SearchFilters(accounts=["personal"]))
    assert result.total == 1
    assert result.messages[0]["account"] == "personal"


def test_search_excludes_deleted_by_default(db):
    _insert(db, maildir_name="m1", deleted=False)
    _insert(db, maildir_name="m2", deleted=True)

    default = search(db, SearchFilters(accounts=["personal"]))
    assert default.total == 1
    assert default.messages[0]["deleted"] is False

    with_deleted = search(db, SearchFilters(accounts=["personal"], include_deleted=True))
    assert with_deleted.total == 2


def test_search_has_attachment_filter(db):
    _insert(db, maildir_name="m1", has_attachment=True,
            attachments=[{"filename": "a.pdf", "content_type": "application/pdf",
                           "size_bytes": 1, "content_disposition": "attachment"}])
    _insert(db, maildir_name="m2", has_attachment=False)

    only_attach = search(db, SearchFilters(accounts=["personal"], has_attachment=True))
    assert only_attach.total == 1
    assert only_attach.messages[0]["has_attachment"] is True

    only_none = search(db, SearchFilters(accounts=["personal"], has_attachment=False))
    assert only_none.total == 1
    assert only_none.messages[0]["has_attachment"] is False

    either = search(db, SearchFilters(accounts=["personal"]))
    assert either.total == 2


def test_search_since_until_inclusive(db):
    _insert(db, maildir_name="early", date_utc="2026-09-01T00:00:00Z")
    _insert(db, maildir_name="boundary_since", date_utc="2026-09-02T00:00:00Z")
    _insert(db, maildir_name="boundary_until", date_utc="2026-09-03T23:59:59Z")
    _insert(db, maildir_name="late", date_utc="2026-09-04T00:00:01Z")

    result = search(db, SearchFilters(
        accounts=["personal"],
        since="2026-09-02T00:00:00Z",
        until="2026-09-03T23:59:59Z",
    ))
    assert result.total == 2
    dates = sorted(m["date"] for m in result.messages)
    assert dates == ["2026-09-02T00:00:00Z", "2026-09-03T23:59:59Z"]


def test_search_exclude_folder(db):
    _insert(db, folder="INBOX", maildir_name="m1")
    _insert(db, folder="Trash", maildir_name="m2")

    result = search(db, SearchFilters(accounts=["personal"], exclude_folders=["Trash"]))
    assert result.total == 1
    assert result.messages[0]["folder"] == "INBOX"


def test_search_exclude_account_folder_pairs(db):
    _insert(db, account="personal", folder="Promotions", maildir_name="m1")
    _insert(db, account="work", folder="Promotions", maildir_name="m2")

    result = search(db, SearchFilters(
        accounts=["personal", "work"],
        exclude_account_folder_pairs={("work", "Promotions")},
    ))
    assert result.total == 1
    assert result.messages[0]["account"] == "personal"


def test_search_full_text_matches_subject_and_body(db):
    _insert(db, maildir_name="m1", subject="Invoice for August", body="see attached invoice")
    _insert(db, maildir_name="m2", subject="Dinner plans", body="lets grab dinner")

    result = search(db, SearchFilters(accounts=["personal"], q="invoice"))
    assert result.total == 1
    assert result.messages[0]["subject"] == "Invoice for August"


def test_search_order_date_desc_and_asc(db):
    _insert(db, maildir_name="m1", date_utc="2026-09-01T00:00:00Z")
    _insert(db, maildir_name="m2", date_utc="2026-09-03T00:00:00Z")
    _insert(db, maildir_name="m3", date_utc="2026-09-02T00:00:00Z")

    desc = search(db, SearchFilters(accounts=["personal"], order="date_desc"))
    assert [m["date"] for m in desc.messages] == [
        "2026-09-03T00:00:00Z", "2026-09-02T00:00:00Z", "2026-09-01T00:00:00Z"
    ]

    asc = search(db, SearchFilters(accounts=["personal"], order="date_asc"))
    assert [m["date"] for m in asc.messages] == [
        "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", "2026-09-03T00:00:00Z"
    ]


def test_search_from_substring(db):
    _insert(db, maildir_name="m1", from_raw="Alice Smith <alice@example.com>")
    _insert(db, maildir_name="m2", from_raw="Bob Jones <bob@example.com>")

    result = search(db, SearchFilters(accounts=["personal"], from_="alice"))
    assert result.total == 1
    assert result.messages[0]["from"] == "Alice Smith <alice@example.com>"


def test_search_to_substring(db):
    _insert(db, maildir_name="m1", to_raw="charlie@example.com")
    _insert(db, maildir_name="m2", to_raw="someone-else@example.com")

    result = search(db, SearchFilters(accounts=["personal"], to="charlie"))
    assert result.total == 1


def test_search_subject_substring(db):
    _insert(db, maildir_name="m1", subject="Renewal notice")
    _insert(db, maildir_name="m2", subject="Dinner plans")

    result = search(db, SearchFilters(accounts=["personal"], subject="renewal"))
    assert result.total == 1
    assert result.messages[0]["subject"] == "Renewal notice"


def test_search_seen_filter(db):
    _insert(db, maildir_name="m1", seen=True)
    _insert(db, maildir_name="m2", seen=False)

    unread_only = search(db, SearchFilters(accounts=["personal"], seen=False))
    assert unread_only.total == 1
    assert unread_only.messages[0]["seen"] is False

    read_only = search(db, SearchFilters(accounts=["personal"], seen=True))
    assert read_only.total == 1
    assert read_only.messages[0]["seen"] is True


def test_search_order_relevance_ranks_better_match_first(db):
    _insert(db, maildir_name="m1", subject="unrelated", body="mentions invoice only once")
    _insert(db, maildir_name="m2", subject="invoice invoice invoice",
            body="invoice invoice invoice invoice")

    result = search(db, SearchFilters(accounts=["personal"], q="invoice", order="relevance"))
    assert result.total == 2
    assert result.messages[0]["subject"] == "invoice invoice invoice"


def test_account_stats_counts_total_and_unseen(db):
    from mail_archive_server.store import account_stats
    _insert(db, maildir_name="m1", seen=True, deleted=False)
    _insert(db, maildir_name="m2", seen=False, deleted=False)
    _insert(db, maildir_name="m3", seen=False, deleted=True)

    stats = account_stats(db, "personal")
    assert stats["messages"] == 3
    assert stats["unseen"] == 2


def test_account_stats_unknown_account_is_zero(db):
    from mail_archive_server.store import account_stats
    assert account_stats(db, "nonexistent") == {"messages": 0, "unseen": 0}


def test_folder_counts_grouped_by_account_and_folder(db):
    from mail_archive_server.store import folder_counts
    _insert(db, account="personal", folder="INBOX", maildir_name="m1")
    _insert(db, account="personal", folder="INBOX", maildir_name="m2")
    _insert(db, account="personal", folder="Archive", maildir_name="m3")
    _insert(db, account="work", folder="INBOX", maildir_name="m1")

    counts = folder_counts(db, ["personal", "work"])
    by_key = {(c["account"], c["folder"]): c["messages"] for c in counts}
    assert by_key == {
        ("personal", "INBOX"): 2,
        ("personal", "Archive"): 1,
        ("work", "INBOX"): 1,
    }


def test_folder_counts_scoped_to_requested_accounts(db):
    from mail_archive_server.store import folder_counts
    _insert(db, account="personal", folder="INBOX", maildir_name="m1")
    _insert(db, account="work", folder="INBOX", maildir_name="m1")

    counts = folder_counts(db, ["personal"])
    assert {(c["account"], c["folder"]) for c in counts} == {("personal", "INBOX")}


def test_search_pagination_total_reflects_full_match_count(db):
    for i in range(5):
        _insert(db, maildir_name=f"m{i}", date_utc=f"2026-09-0{i+1}T00:00:00Z")

    page = search(db, SearchFilters(accounts=["personal"], limit=2, offset=0, order="date_asc"))
    assert page.total == 5
    assert len(page.messages) == 2
    assert page.messages[0]["date"] == "2026-09-01T00:00:00Z"

    page2 = search(db, SearchFilters(accounts=["personal"], limit=2, offset=2, order="date_asc"))
    assert page2.messages[0]["date"] == "2026-09-03T00:00:00Z"

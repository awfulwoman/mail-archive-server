from __future__ import annotations
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from mail_archive_server.config import load_config
from mail_archive_server.http import create_app
from mail_archive_server.indexer import reindex
from mail_archive_server.store import open_db, set_meta
from tests.conftest import install_fake_mbsync


def _make_folder(root: Path, *parts: str) -> Path:
    folder = root.joinpath(*parts)
    (folder / "cur").mkdir(parents=True)
    (folder / "new").mkdir(parents=True)
    (folder / "tmp").mkdir(parents=True)
    return folder


def _write_message(path: Path, *, subject="Hello", from_addr="a@b.com",
                    date="Tue, 01 Sep 2026 09:15:04 +0000", body="body text") -> None:
    raw = (
        f"From: {from_addr}\r\nSubject: {subject}\r\nDate: {date}\r\n\r\n{body}"
    )
    path.write_bytes(raw.encode())


def _seed_sync_status(conn, account: str, *, ok: bool = True, attempted_at: str = "2026-09-01T00:00:00Z") -> None:
    set_meta(conn, f"last_sync_attempt_at:{account}", attempted_at)
    set_meta(conn, f"last_sync_ok:{account}", "1" if ok else "0")


@pytest.fixture
def env(tmp_path):
    maildir = tmp_path / "maildir"
    personal_inbox = _make_folder(maildir / "personal", "INBOX")
    _write_message(personal_inbox / "cur" / "1.uniq:2,S", subject="Personal message")
    work_inbox = _make_folder(maildir / "work", "INBOX")
    _write_message(work_inbox / "cur" / "1.uniq:2,S", subject="Work message")

    config = load_config({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_TOKENS__wildcard__SECRET": "wildcard-secret",
        "MAIL_ARCHIVE_TOKENS__wildcard__ACCOUNTS": "*",
        "MAIL_ARCHIVE_TOKENS__personal_only__SECRET": "personal-secret",
        "MAIL_ARCHIVE_TOKENS__personal_only__ACCOUNTS": "personal",
    })

    conn = open_db(Path(":memory:"))
    reindex(conn, maildir)
    _seed_sync_status(conn, "personal")
    _seed_sync_status(conn, "work")

    app = create_app(config, conn, maildir)
    client = TestClient(app)
    return {"client": client, "conn": conn, "maildir": maildir}


def auth(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def test_health_requires_no_auth(env):
    resp = env["client"].get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_missing_auth_header_401(env):
    resp = env["client"].get("/accounts")
    assert resp.status_code == 401


def test_invalid_token_401(env):
    resp = env["client"].get("/accounts", headers=auth("not-a-real-token"))
    assert resp.status_code == 401


def test_accounts_scoped_token_sees_only_its_accounts(env):
    resp = env["client"].get("/accounts", headers=auth("personal-secret"))
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()["accounts"]}
    assert names == {"personal"}


def test_accounts_wildcard_token_sees_all(env):
    resp = env["client"].get("/accounts", headers=auth("wildcard-secret"))
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()["accounts"]}
    assert names == {"personal", "work"}


def test_messages_no_account_param_restricted_to_token_scope(env):
    resp = env["client"].get("/messages", headers=auth("personal-secret"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["accounts_searched"] == ["personal"]
    assert all(m["account"] == "personal" for m in body["messages"])


def test_messages_unknown_account_400(env):
    resp = env["client"].get("/messages", params={"account": "ghost"}, headers=auth("wildcard-secret"))
    assert resp.status_code == 400


def test_messages_out_of_scope_known_account_403(env):
    resp = env["client"].get("/messages", params={"account": "work"}, headers=auth("personal-secret"))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "account_forbidden"


def test_message_detail_out_of_scope_returns_404_not_403(env):
    work_msg = env["client"].get("/messages", params={"account": "work"}, headers=auth("wildcard-secret")).json()
    work_id = work_msg["messages"][0]["id"]

    resp = env["client"].get(f"/messages/{work_id}", headers=auth("personal-secret"))
    assert resp.status_code == 404


def test_message_detail_unknown_id_404(env):
    resp = env["client"].get("/messages/doesnotexist", headers=auth("wildcard-secret"))
    assert resp.status_code == 404


def test_message_detail_in_scope_returns_body(env):
    listing = env["client"].get("/messages", params={"account": "personal"}, headers=auth("personal-secret")).json()
    id_ = listing["messages"][0]["id"]

    resp = env["client"].get(f"/messages/{id_}", headers=auth("personal-secret"))
    assert resp.status_code == 200
    assert resp.json()["body"] == "body text"


def test_reindex_requires_wildcard_token(env):
    resp = env["client"].post("/reindex", headers=auth("personal-secret"))
    assert resp.status_code == 403


def test_reindex_wildcard_token_runs_and_picks_up_new_mail(env):
    new_inbox = env["maildir"] / "personal" / "INBOX"
    _write_message(new_inbox / "cur" / "2.uniq:2,S", subject="Second message")

    resp = env["client"].post("/reindex", headers=auth("wildcard-secret"))
    assert resp.status_code == 202
    assert resp.json()["indexing"] is True

    listing = env["client"].get("/messages", params={"account": "personal"}, headers=auth("wildcard-secret")).json()
    assert listing["total"] == 2


def test_status_shape(env):
    resp = env["client"].get("/status", headers=auth("wildcard-secret"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == 2
    assert {a["name"] for a in body["accounts"]} == {"personal", "work"}
    assert isinstance(body["stale_seconds"], int)
    assert body["indexing"] is False


def test_status_reports_sync_ok_and_attempt_per_account(env):
    resp = env["client"].get("/status", headers=auth("wildcard-secret"))
    accounts = {a["name"]: a for a in resp.json()["accounts"]}
    assert accounts["personal"]["last_sync_ok"] is True
    assert accounts["personal"]["last_sync_attempt_at"] == "2026-09-01T00:00:00Z"


def test_status_reports_sync_failure(env):
    _seed_sync_status(env["conn"], "work", ok=False, attempted_at="2026-09-02T00:00:00Z")
    resp = env["client"].get("/status", headers=auth("wildcard-secret"))
    accounts = {a["name"]: a for a in resp.json()["accounts"]}
    assert accounts["work"]["last_sync_ok"] is False


def test_accounts_never_synced_report_null_sync_fields(tmp_path):
    maildir = tmp_path / "maildir"
    inbox = _make_folder(maildir / "readonly", "INBOX")
    _write_message(inbox / "cur" / "1.uniq:2,S")

    config = load_config({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_TOKENS__wildcard__SECRET": "s1",
    })
    conn = open_db(Path(":memory:"))
    reindex(conn, maildir)
    client = TestClient(create_app(config, conn, maildir))

    resp = client.get("/accounts", headers=auth("s1"))
    acct = resp.json()["accounts"][0]
    assert acct["last_sync_ok"] is None
    assert acct["last_sync_attempt_at"] is None
    assert acct["stale_seconds"] is None


def test_status_scoped_to_token(env):
    resp = env["client"].get("/status", headers=auth("personal-secret"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == 1
    assert {a["name"] for a in body["accounts"]} == {"personal"}


def test_folders_filtered_by_scope(env):
    resp = env["client"].get("/folders", headers=auth("personal-secret"))
    assert resp.status_code == 200
    accounts_in_response = {f["account"] for f in resp.json()["folders"]}
    assert accounts_in_response == {"personal"}


def test_folders_filtered_by_account_param(env):
    resp = env["client"].get("/folders", params={"account": "work"}, headers=auth("wildcard-secret"))
    assert resp.status_code == 200
    folders = resp.json()["folders"]
    assert all(f["account"] == "work" for f in folders)
    assert any(f["folder"] == "INBOX" for f in folders)


def test_messages_relevance_order_without_q_is_400(env):
    resp = env["client"].get("/messages", params={"order": "relevance"}, headers=auth("wildcard-secret"))
    assert resp.status_code == 400


def test_messages_response_envelope_shape(env):
    resp = env["client"].get("/messages", headers=auth("wildcard-secret"))
    body = resp.json()
    assert set(["messages", "total", "limit", "offset", "accounts_searched", "index"]) <= set(body.keys())
    assert set(["last_indexed_at", "oldest_sync_at", "stale_seconds"]) <= set(body["index"].keys())


def test_post_sync_requires_wildcard_token(env):
    resp = env["client"].post("/sync", headers=auth("personal-secret"))
    assert resp.status_code == 403


def test_post_sync_missing_auth_401(env):
    resp = env["client"].post("/sync")
    assert resp.status_code == 401


def test_never_successfully_synced_account_still_visible_in_status_and_accounts(tmp_path):
    """A configured account whose sync has NEVER once succeeded — wrong password,
    unreachable host — must still be visible in /accounts and /status, and its
    failure must be reported (last_sync_ok False), not silent."""
    maildir = tmp_path / "maildir"
    maildir.mkdir()
    mbsync_bin = install_fake_mbsync(tmp_path)
    marker_dir = tmp_path / "fake_mbsync_markers"
    marker_dir.mkdir()
    (marker_dir / "personal.fail").touch()  # mbsync always exits non-zero

    config = load_config({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_TOKENS__wildcard__SECRET": "wildcard-secret",
        "MAIL_ARCHIVE_TOKENS__wildcard__ACCOUNTS": "*",
        "MAIL_ARCHIVE_MBSYNC_BIN": str(mbsync_bin),
        "MAIL_ARCHIVE_MBSYNCRC_PATH": str(tmp_path / "mbsyncrc"),
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__HOST": "imap.example.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__USERNAME": "u",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__PASSWORD": "p",
    })
    conn = open_db(Path(":memory:"))
    from mail_archive_server.pipeline import sync_and_reindex
    sync_and_reindex(conn, config)

    client = TestClient(create_app(config, conn, maildir))

    # The sync attempt creates the (empty) account store dir before invoking
    # mbsync, so reindex discovers it — last_indexed_at is set, message counts
    # are still zero.
    accounts_resp = client.get("/accounts", headers=auth("wildcard-secret")).json()
    assert accounts_resp["accounts"] == [{
        "name": "personal",
        "messages": 0,
        "folders": 0,
        "unseen": 0,
        "last_sync_attempt_at": accounts_resp["accounts"][0]["last_sync_attempt_at"],
        "last_sync_ok": False,
        "last_indexed_at": accounts_resp["accounts"][0]["last_indexed_at"],
        "stale_seconds": accounts_resp["accounts"][0]["stale_seconds"],
    }]
    assert accounts_resp["accounts"][0]["last_sync_attempt_at"] is not None

    status_resp = client.get("/status", headers=auth("wildcard-secret")).json()
    assert {a["name"] for a in status_resp["accounts"]} == {"personal"}

    # Searching it explicitly must not 400 as "unknown" — it IS a real,
    # configured account, just one with nothing indexed yet.
    search_resp = client.get("/messages", params={"account": "personal"}, headers=auth("wildcard-secret"))
    assert search_resp.status_code == 200
    assert search_resp.json()["total"] == 0


def test_scheduler_syncs_promptly_on_startup_when_enabled(tmp_path):
    import time

    maildir = tmp_path / "maildir"
    maildir.mkdir()
    mbsync_bin = install_fake_mbsync(tmp_path)
    (tmp_path / "maildir_path.txt").write_text(str(maildir))

    config = load_config({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_TOKENS__wildcard__SECRET": "wildcard-secret",
        "MAIL_ARCHIVE_TOKENS__wildcard__ACCOUNTS": "*",
        "MAIL_ARCHIVE_MBSYNC_BIN": str(mbsync_bin),
        "MAIL_ARCHIVE_MBSYNCRC_PATH": str(tmp_path / "mbsyncrc"),
        "MAIL_ARCHIVE_SYNC_INTERVAL_SECONDS": "3600",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__HOST": "imap.example.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__USERNAME": "u",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__PASSWORD": "p",
    })
    conn = open_db(Path(":memory:"))
    app = create_app(config, conn, maildir, run_scheduler=True)

    with TestClient(app) as client:
        # No `account` filter: this never 400s while the account is still
        # unknown, unlike naming "personal" before it has been indexed once.
        deadline = time.monotonic() + 3
        total = 0
        while time.monotonic() < deadline:
            listing = client.get("/messages", headers=auth("wildcard-secret")).json()
            total = listing["total"]
            if total == 1:
                break
            time.sleep(0.05)
        assert total == 1


def test_scheduler_not_started_by_default(tmp_path):
    import time

    maildir = tmp_path / "maildir"
    maildir.mkdir()
    mbsync_bin = install_fake_mbsync(tmp_path)
    (tmp_path / "maildir_path.txt").write_text(str(maildir))

    config = load_config({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_TOKENS__wildcard__SECRET": "wildcard-secret",
        "MAIL_ARCHIVE_MBSYNC_BIN": str(mbsync_bin),
        "MAIL_ARCHIVE_MBSYNCRC_PATH": str(tmp_path / "mbsyncrc"),
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__HOST": "imap.example.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__USERNAME": "u",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__PASSWORD": "p",
    })
    conn = open_db(Path(":memory:"))
    app = create_app(config, conn, maildir)  # run_scheduler defaults to False

    with TestClient(app) as client:
        time.sleep(0.3)
        resp = client.get("/accounts", headers=auth("wildcard-secret"))
        # "personal" is configured, so it's listed — but nothing ever synced it.
        [acct] = resp.json()["accounts"]
        assert acct["name"] == "personal"
        assert acct["last_sync_attempt_at"] is None
        assert acct["last_sync_ok"] is None
    assert not config.mbsyncrc_path.exists()


def test_post_sync_runs_full_pipeline_and_picks_up_synced_mail(tmp_path):
    maildir = tmp_path / "maildir"
    maildir.mkdir()
    mbsync_bin = install_fake_mbsync(tmp_path)
    (tmp_path / "maildir_path.txt").write_text(str(maildir))

    config = load_config({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_TOKENS__wildcard__SECRET": "wildcard-secret",
        "MAIL_ARCHIVE_TOKENS__wildcard__ACCOUNTS": "*",
        "MAIL_ARCHIVE_MBSYNC_BIN": str(mbsync_bin),
        "MAIL_ARCHIVE_MBSYNCRC_PATH": str(tmp_path / "mbsyncrc"),
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__HOST": "imap.example.com",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__USERNAME": "u",
        "MAIL_ARCHIVE_IMAP_ACCOUNTS__personal__PASSWORD": "p",
    })
    conn = open_db(Path(":memory:"))
    client = TestClient(create_app(config, conn, maildir))

    resp = client.post("/sync", headers=auth("wildcard-secret"))
    assert resp.status_code == 202
    assert resp.json()["indexing"] is True

    listing = client.get("/messages", params={"account": "personal"}, headers=auth("wildcard-secret")).json()
    assert listing["total"] == 1
    assert listing["messages"][0]["subject"] == "Synced personal"

    status = client.get("/status", headers=auth("wildcard-secret")).json()
    accounts = {a["name"]: a for a in status["accounts"]}
    assert accounts["personal"]["last_sync_ok"] is True

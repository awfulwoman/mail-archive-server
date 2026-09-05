from __future__ import annotations
import os
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from mail_archive_server.config import load_config
from mail_archive_server.http import create_app
from mail_archive_server.indexer import reindex
from mail_archive_server.store import open_db


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


def _touch_state(mbsync_root: Path, account: str, mtime: float = 1_700_000_000.0) -> None:
    d = mbsync_root / account
    d.mkdir(parents=True, exist_ok=True)
    f = d / "INBOX"
    f.write_text("state")
    os.utime(f, (mtime, mtime))


@pytest.fixture
def env(tmp_path):
    maildir = tmp_path / "maildir"
    personal_inbox = _make_folder(maildir / "personal", "INBOX")
    _write_message(personal_inbox / "cur" / "1.uniq:2,S", subject="Personal message")
    work_inbox = _make_folder(maildir / "work", "INBOX")
    _write_message(work_inbox / "cur" / "1.uniq:2,S", subject="Work message")

    mbsync_root = maildir / ".mbsync"
    _touch_state(mbsync_root, "personal")
    _touch_state(mbsync_root, "work")

    config = load_config({
        "MAIL_ARCHIVE_MAILDIR_PATH": str(maildir),
        "MAIL_ARCHIVE_TOKENS__wildcard__SECRET": "wildcard-secret",
        "MAIL_ARCHIVE_TOKENS__wildcard__ACCOUNTS": "*",
        "MAIL_ARCHIVE_TOKENS__personal_only__SECRET": "personal-secret",
        "MAIL_ARCHIVE_TOKENS__personal_only__ACCOUNTS": "personal",
    })

    conn = open_db(Path(":memory:"))
    reindex(conn, maildir)

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

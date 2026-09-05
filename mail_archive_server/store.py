from __future__ import annotations
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id            TEXT PRIMARY KEY,
  account       TEXT NOT NULL,
  folder        TEXT NOT NULL,
  path          TEXT NOT NULL,
  maildir_name  TEXT NOT NULL,
  message_id    TEXT,
  from_name     TEXT,
  from_addr     TEXT,
  from_raw      TEXT,
  to_raw        TEXT,
  cc_raw        TEXT,
  subject       TEXT,
  date_utc      TEXT,
  size_bytes    INTEGER,
  flags         TEXT,
  seen          INTEGER NOT NULL DEFAULT 0,
  deleted       INTEGER NOT NULL DEFAULT 0,
  has_attachment INTEGER NOT NULL DEFAULT 0,
  attachments   TEXT,
  body_preview  TEXT,
  indexed_at    TEXT NOT NULL,
  mtime         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_account ON messages(account);
CREATE INDEX IF NOT EXISTS idx_msg_date    ON messages(date_utc DESC);
CREATE INDEX IF NOT EXISTS idx_msg_folder  ON messages(account, folder);
CREATE INDEX IF NOT EXISTS idx_msg_from    ON messages(from_addr);
CREATE INDEX IF NOT EXISTS idx_msg_msgid   ON messages(message_id);
CREATE INDEX IF NOT EXISTS idx_msg_attach  ON messages(has_attachment);
CREATE INDEX IF NOT EXISTS idx_msg_deleted ON messages(deleted);
CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_path ON messages(path);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  id UNINDEXED, subject, from_text, to_text, body,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def compute_id(account: str, folder: str, maildir_name: str) -> str:
    digest = hashlib.sha256(f"{account}\0{folder}\0{maildir_name}".encode()).hexdigest()
    return digest[:16]


def open_db(path: Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    # The HTTP server may dispatch a request handler on a different thread than the
    # one that opened this connection; access is still serialized (one connection,
    # no concurrent writers here), so this is safe.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_message(
    conn: sqlite3.Connection,
    *,
    id: str,
    account: str,
    folder: str,
    path: str,
    maildir_name: str,
    message_id: str,
    from_raw: str,
    from_addr: str,
    to_raw: str,
    cc_raw: str,
    subject: str,
    date_utc: str,
    size_bytes: int,
    flags: str,
    seen: bool,
    deleted: bool,
    has_attachment: bool,
    attachments: list[dict],
    body_preview: str,
    body: str,
    indexed_at: str,
    from_name: str = "",
    mtime: float = 0.0,
) -> None:
    conn.execute(
        """
        INSERT INTO messages (
          id, account, folder, path, maildir_name, message_id, from_name, from_addr,
          from_raw, to_raw, cc_raw, subject, date_utc, size_bytes, flags, seen,
          deleted, has_attachment, attachments, body_preview, indexed_at, mtime
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            id, account, folder, path, maildir_name, message_id, from_name, from_addr,
            from_raw, to_raw, cc_raw, subject, date_utc, size_bytes, flags, int(seen),
            int(deleted), int(has_attachment), json.dumps(attachments), body_preview,
            indexed_at, mtime,
        ),
    )
    conn.execute(
        "INSERT INTO messages_fts (id, subject, from_text, to_text, body) VALUES (?,?,?,?,?)",
        (id, subject, from_raw, to_raw, body),
    )
    conn.commit()


def update_flags(
    conn: sqlite3.Connection, *, id: str, path: str, flags: str, seen: bool, deleted: bool
) -> None:
    conn.execute(
        "UPDATE messages SET path=?, flags=?, seen=?, deleted=? WHERE id=?",
        (path, flags, int(seen), int(deleted), id),
    )
    conn.commit()


def delete_ids(conn: sqlite3.Connection, ids: list[str]) -> None:
    if not ids:
        return
    conn.executemany("DELETE FROM messages WHERE id=?", [(i,) for i in ids])
    conn.executemany("DELETE FROM messages_fts WHERE id=?", [(i,) for i in ids])
    conn.commit()


def ids_for_account(conn: sqlite3.Connection, account: str) -> dict[str, str]:
    rows = conn.execute("SELECT id, path FROM messages WHERE account=?", (account,)).fetchall()
    return {r["id"]: r["path"] for r in rows}


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def known_accounts(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT key FROM meta WHERE key LIKE 'last_indexed_at:%'").fetchall()
    return {r["key"].split(":", 1)[1] for r in rows}


def _row_to_dict(row: sqlite3.Row, *, body: str | None = None) -> dict:
    d = {
        "id": row["id"],
        "account": row["account"],
        "message_id": row["message_id"],
        "folder": row["folder"],
        "from": row["from_raw"],
        "from_addr": row["from_addr"],
        "to": row["to_raw"],
        "subject": row["subject"],
        "date": row["date_utc"],
        "seen": bool(row["seen"]),
        "deleted": bool(row["deleted"]),
        "size_bytes": row["size_bytes"],
        "has_attachment": bool(row["has_attachment"]),
        "attachments": json.loads(row["attachments"]) if row["attachments"] else [],
        "body_preview": row["body_preview"],
    }
    if body is not None:
        d["body"] = body
    return d


def account_stats(conn: sqlite3.Connection, account: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS messages, SUM(CASE WHEN seen = 0 THEN 1 ELSE 0 END) AS unseen "
        "FROM messages WHERE account = ?",
        (account,),
    ).fetchone()
    return {"messages": row["messages"] or 0, "unseen": row["unseen"] or 0}


def folder_counts(conn: sqlite3.Connection, accounts: list[str]) -> list[dict]:
    if not accounts:
        return []
    placeholders = ",".join("?" * len(accounts))
    rows = conn.execute(
        f"SELECT account, folder, COUNT(*) AS messages FROM messages "
        f"WHERE account IN ({placeholders}) GROUP BY account, folder",
        accounts,
    ).fetchall()
    return [{"account": r["account"], "folder": r["folder"], "messages": r["messages"]} for r in rows]


def get_message(conn: sqlite3.Connection, id: str) -> dict | None:
    row = conn.execute("SELECT * FROM messages WHERE id=?", (id,)).fetchone()
    if row is None:
        return None
    body_row = conn.execute("SELECT body FROM messages_fts WHERE id=?", (id,)).fetchone()
    body = body_row["body"] if body_row else ""
    return _row_to_dict(row, body=body)


@dataclass
class SearchFilters:
    accounts: list[str]
    q: str | None = None
    from_: str | None = None
    to: str | None = None
    subject: str | None = None
    folders: list[str] | None = None
    exclude_folders: list[str] = field(default_factory=list)
    exclude_account_folder_pairs: set[tuple[str, str]] = field(default_factory=set)
    since: str | None = None
    until: str | None = None
    has_attachment: bool | None = None
    attachment_name: str | None = None
    seen: bool | None = None
    include_deleted: bool = False
    limit: int = 25
    offset: int = 0
    order: str = "date_desc"


@dataclass
class SearchResult:
    messages: list[dict]
    total: int


def search(conn: sqlite3.Connection, f: SearchFilters) -> SearchResult:
    where: list[str] = []
    params: list = []

    if not f.accounts:
        return SearchResult(messages=[], total=0)
    where.append(f"m.account IN ({','.join('?' * len(f.accounts))})")
    params.extend(f.accounts)

    if not f.include_deleted:
        where.append("m.deleted = 0")

    if f.folders:
        where.append(f"m.folder IN ({','.join('?' * len(f.folders))})")
        params.extend(f.folders)

    if f.exclude_folders:
        where.append(f"m.folder NOT IN ({','.join('?' * len(f.exclude_folders))})")
        params.extend(f.exclude_folders)

    for account, folder in f.exclude_account_folder_pairs:
        where.append("NOT (m.account = ? AND m.folder = ?)")
        params.extend([account, folder])

    if f.since:
        where.append("m.date_utc >= ?")
        params.append(f.since)
    if f.until:
        where.append("m.date_utc <= ?")
        params.append(f.until)

    if f.has_attachment is not None:
        where.append("m.has_attachment = ?")
        params.append(int(f.has_attachment))

    if f.seen is not None:
        where.append("m.seen = ?")
        params.append(int(f.seen))

    if f.from_:
        where.append("m.from_raw LIKE ?")
        params.append(f"%{f.from_}%")
    if f.to:
        where.append("m.to_raw LIKE ?")
        params.append(f"%{f.to}%")
    if f.subject:
        where.append("m.subject LIKE ?")
        params.append(f"%{f.subject}%")

    if f.attachment_name:
        where.append("m.attachments LIKE ?")
        params.append(f"%{f.attachment_name}%")

    from_clause = "FROM messages m"
    if f.q:
        from_clause = "FROM messages m JOIN messages_fts ON messages_fts.id = m.id"
        where.append("messages_fts MATCH ?")
        params.append(f.q)

    where_sql = " AND ".join(where) if where else "1=1"

    total = conn.execute(
        f"SELECT COUNT(*) AS n {from_clause} WHERE {where_sql}", params
    ).fetchone()["n"]

    if f.order == "relevance" and f.q:
        order_sql = "ORDER BY bm25(messages_fts) ASC"
    elif f.order == "date_asc":
        order_sql = "ORDER BY m.date_utc ASC"
    else:
        order_sql = "ORDER BY m.date_utc DESC"

    rows = conn.execute(
        f"SELECT m.* {from_clause} WHERE {where_sql} {order_sql} LIMIT ? OFFSET ?",
        [*params, f.limit, f.offset],
    ).fetchall()

    return SearchResult(messages=[_row_to_dict(r) for r in rows], total=total)

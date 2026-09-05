from __future__ import annotations
import sqlite3
import threading
from pathlib import Path
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from mail_archive_server.config import Config, Token
from mail_archive_server.indexer import reindex as run_reindex
from mail_archive_server.store import (
    SearchFilters,
    account_stats,
    folder_counts,
    get_message,
    get_meta,
    known_accounts,
    search,
)
from mail_archive_server.timeutil import mtime_to_utc, normalize_since, normalize_until, stale_seconds


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _get_token(request: Request) -> Token | None:
    config: Config = request.app.state.config
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    secret = header[len("Bearer "):]
    return config.token_for_secret(secret)


def _account_last_sync_at(mbsync_state_path: Path, account: str) -> str | None:
    d = mbsync_state_path / account
    if not d.is_dir():
        return None
    newest: float | None = None
    for f in d.iterdir():
        if f.is_file():
            m = f.stat().st_mtime
            if newest is None or m > newest:
                newest = m
    return mtime_to_utc(newest) if newest is not None else None


def _account_summary(request: Request, account: str) -> dict:
    conn: sqlite3.Connection = request.app.state.conn
    config: Config = request.app.state.config
    stats = account_stats(conn, account)
    last_indexed_at = get_meta(conn, f"last_indexed_at:{account}")
    last_sync_at = _account_last_sync_at(config.mbsync_state_path, account)
    folders = folder_counts(conn, [account])
    return {
        "name": account,
        "messages": stats["messages"],
        "folders": len(folders),
        "unseen": stats["unseen"],
        "last_sync_at": last_sync_at,
        "last_indexed_at": last_indexed_at,
        "stale_seconds": stale_seconds(last_sync_at),
    }


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def get_status(request: Request) -> JSONResponse:
    token = _get_token(request)
    if token is None:
        return _error(401, "unauthorized", "missing or invalid bearer token")

    conn: sqlite3.Connection = request.app.state.conn
    config: Config = request.app.state.config
    allowed = sorted(a for a in known_accounts(conn) if token.allows(a))
    summaries = [_account_summary(request, a) for a in allowed]
    worst = max((s["stale_seconds"] for s in summaries if s["stale_seconds"] is not None), default=None)

    return JSONResponse({
        "maildir_path": str(config.maildir_path),
        "messages": sum(s["messages"] for s in summaries),
        "accounts": summaries,
        "stale_seconds": worst,
        "indexing": request.app.state.indexing,
    })


async def get_accounts(request: Request) -> JSONResponse:
    token = _get_token(request)
    if token is None:
        return _error(401, "unauthorized", "missing or invalid bearer token")

    conn: sqlite3.Connection = request.app.state.conn
    allowed = sorted(a for a in known_accounts(conn) if token.allows(a))
    return JSONResponse({"accounts": [_account_summary(request, a) for a in allowed]})


async def get_folders(request: Request) -> JSONResponse:
    token = _get_token(request)
    if token is None:
        return _error(401, "unauthorized", "missing or invalid bearer token")

    conn: sqlite3.Connection = request.app.state.conn
    known = known_accounts(conn)
    requested = request.query_params.getlist("account")

    if requested:
        for a in requested:
            if a not in known:
                return _error(400, "unknown_account", f"unknown account: {a}")
            if not token.allows(a):
                return _error(403, "account_forbidden", f"token not scoped to account: {a}")
        accounts = requested
    else:
        accounts = sorted(a for a in known if token.allows(a))

    return JSONResponse({"folders": folder_counts(conn, accounts)})


def _resolve_accounts(request: Request, token: Token) -> list[str] | JSONResponse:
    conn: sqlite3.Connection = request.app.state.conn
    known = known_accounts(conn)
    requested = request.query_params.getlist("account")

    if not requested:
        return sorted(a for a in known if token.allows(a))

    for a in requested:
        if a not in known:
            return _error(400, "unknown_account", f"unknown account: {a}")
    for a in requested:
        if not token.allows(a):
            return _error(403, "account_forbidden", f"token not scoped to account: {a}")
    return requested


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in ("1", "true", "yes")


async def get_messages(request: Request) -> JSONResponse:
    token = _get_token(request)
    if token is None:
        return _error(401, "unauthorized", "missing or invalid bearer token")

    accounts = _resolve_accounts(request, token)
    if isinstance(accounts, JSONResponse):
        return accounts

    qp = request.query_params
    order = qp.get("order", "date_desc")
    q = qp.get("q")
    if order == "relevance" and not q:
        return _error(400, "invalid_order", "order=relevance requires q")

    exclude_accounts = set(qp.getlist("exclude_account"))
    accounts = [a for a in accounts if a not in exclude_accounts]

    since = qp.get("since")
    until = qp.get("until")

    filters = SearchFilters(
        accounts=accounts,
        q=q,
        from_=qp.get("from"),
        to=qp.get("to"),
        subject=qp.get("subject"),
        folders=qp.getlist("folder") or None,
        exclude_folders=qp.getlist("exclude_folder") or [],
        since=normalize_since(since) if since else None,
        until=normalize_until(until) if until else None,
        has_attachment=_parse_bool(qp.get("has_attachment")),
        attachment_name=qp.get("attachment_name"),
        seen=_parse_bool(qp.get("seen")),
        include_deleted=_parse_bool(qp.get("include_deleted")) or False,
        limit=int(qp.get("limit", "25")),
        offset=int(qp.get("offset", "0")),
        order=order,
    )
    result = search(request.app.state.conn, filters)

    conn: sqlite3.Connection = request.app.state.conn
    config: Config = request.app.state.config
    last_indexed = [get_meta(conn, f"last_indexed_at:{a}") for a in accounts]
    last_indexed = [x for x in last_indexed if x]
    sync_ats = [_account_last_sync_at(config.mbsync_state_path, a) for a in accounts]
    sync_ats = [x for x in sync_ats if x]
    worst_stale = max((stale_seconds(s) for s in sync_ats), default=None)

    return JSONResponse({
        "messages": result.messages,
        "total": result.total,
        "limit": filters.limit,
        "offset": filters.offset,
        "accounts_searched": accounts,
        "index": {
            "last_indexed_at": max(last_indexed) if last_indexed else None,
            "oldest_sync_at": min(sync_ats) if sync_ats else None,
            "stale_seconds": worst_stale,
        },
    })


async def get_message_detail(request: Request) -> JSONResponse:
    token = _get_token(request)
    if token is None:
        return _error(401, "unauthorized", "missing or invalid bearer token")

    id_ = request.path_params["id"]
    msg = get_message(request.app.state.conn, id_)
    if msg is None or not token.allows(msg["account"]):
        return _error(404, "not_found", "no such message")
    return JSONResponse(msg)


def _do_reindex(app_state, maildir_path: Path, accounts: list[str] | None) -> None:
    with app_state.indexing_lock:
        app_state.indexing = True
        try:
            run_reindex(app_state.conn, maildir_path, accounts)
        finally:
            app_state.indexing = False


async def post_reindex(request: Request) -> JSONResponse:
    token = _get_token(request)
    if token is None:
        return _error(401, "unauthorized", "missing or invalid bearer token")
    if token.accounts is not None:
        return _error(403, "reindex_forbidden", "reindex requires a wildcard-scoped token")

    account = request.query_params.get("account")
    config: Config = request.app.state.config
    _do_reindex(request.app.state, config.maildir_path, [account] if account else None)
    return JSONResponse({"indexing": True}, status_code=202)


def create_app(config: Config, conn: sqlite3.Connection, maildir_root: Path) -> Starlette:
    app = Starlette(routes=[
        Route("/health", health),
        Route("/status", get_status),
        Route("/accounts", get_accounts),
        Route("/folders", get_folders),
        Route("/messages", get_messages),
        Route("/messages/{id}", get_message_detail),
        Route("/reindex", post_reindex, methods=["POST"]),
    ])
    app.state.config = config
    app.state.conn = conn
    app.state.maildir_root = maildir_root
    app.state.indexing = False
    app.state.indexing_lock = threading.Lock()
    return app

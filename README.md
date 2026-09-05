# mail-archive-server

A small, read-only, multi-account REST API in front of an [isync/mbsync](https://isync.sourceforge.io/)
Maildir backup. It indexes the Maildir into a local SQLite+FTS5 database and serves
fast search — full-text, sender, date range, and **attachment presence** — which
plain IMAP `SEARCH` cannot express.

This service owns the index and nothing else does. It never writes to the
Maildir it reads, and any client of this API is expected to hold no mail state
of its own — search, filtering, and attachment metadata all live here, behind
one HTTP boundary.

## Running

```
uv sync
cp .env.example .env   # fill in a real token
uv run mail-archive-server
```

On startup the Maildir is scanned (incremental — see "Indexing" below) before the
server starts accepting requests.

## Configuration

See `.env.example` for the full list. The two that need real thought:

- `MAIL_ARCHIVE_MAILDIR_PATH` — root containing one directory per account. Accounts
  are **discovered** from this directory listing, never configured — add an
  account to the backup and it appears here with no config change.
- `MAIL_ARCHIVE_TOKENS__<label>__SECRET` / `__ACCOUNTS` — see "Token scoping" below.

## Token scoping — the security boundary

This service serves **every account by default**. The bearer token is what
actually restricts a caller to a subset — enforced in the query layer on every
request, not left to the client to self-limit. `ACCOUNTS=*` sees every account
present in the Maildir, including ones added after the token was issued;
`ACCOUNTS=personal,work` pins it to exactly those.

- Missing/unknown token → `401`.
- Naming an account the token can't see → `403 account_forbidden`.
- Fetching a message by id outside the token's scope → `404` (ids are opaque; a
  403 there would confirm the message exists).
- `POST /reindex` requires a `*`-scoped token.

## Indexing

The indexer walks the Maildir and diffs against the SQLite index by a stable id
(`sha256(account\0folder\0maildir_name)[:16]`) — keyed on the Maildir filename
before its `:2,<flags>` suffix, since IMAP flag changes rename the file on disk.
Three outcomes per message:

- **New** → parsed fully (headers, body, attachments) and inserted.
- **Flags changed only** (same id, different path) → `path`/`flags`/`seen`/`deleted`
  updated, **no re-parse**.
- **Gone from disk** → deleted from the index. This sweep is scoped to the
  account(s) actually walked, so a transiently unreadable account can never wipe
  another account's rows.

A steady-state reindex after an hourly mbsync run therefore touches almost nothing.
Trigger it manually with `POST /reindex` (optionally `?account=<name>` for one).

## Deleted-but-retained mail

mbsync here runs with `Expunge None`: mail deleted server-side is retained locally,
flagged `T`, not removed. On the archive this design was built for, that's ~12% of
the corpus, almost entirely sitting in `INBOX` — so `exclude_folder=Trash,Junk`
alone does not filter it out. `GET /messages` therefore excludes `T`-flagged
messages by default; pass `include_deleted=true` to see them. Every returned
message carries `"deleted"` regardless.

## API

```
GET  /health                 no auth
GET  /status                 index + sync freshness, per account
GET  /accounts               accounts the token can see, with counts
GET  /folders                folder counts, optionally ?account=
GET  /messages                search — see below
GET  /messages/{id}          one message including full body
POST /reindex                 incremental scan; ?account= for just one
```

`GET /messages` query parameters: `q`, `account` (repeatable, omit for all in
scope), `exclude_account`, `from`, `to`, `subject`, `folder` (repeatable),
`exclude_folder` (repeatable, default `Trash,Junk`), `since`/`until` (a bare
`YYYY-MM-DD` is inclusive of the whole day), `has_attachment`, `attachment_name`,
`seen`, `include_deleted`, `limit` (default 25, max 200), `offset`, `order`
(`date_desc` default, `date_asc`, or `relevance` — the last requires `q`).

## Development

```
uv sync
uv run pytest
```

Built test-first throughout: every module has a red run (test written, fails
because the code doesn't exist yet) before the implementation that turns it green.
`tests/test_http.py` in particular exercises the full auth/scope matrix directly —
a scoping bug is the one defect that would leak mail to a caller who shouldn't see
it, and it can't be caught from the consuming client's own tests.

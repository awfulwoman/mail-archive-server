# mail-archive-server

A small, multi-account service that syncs one or more IMAP mailboxes to a local
Maildir (via [isync/mbsync](https://isync.sourceforge.io/), which it orchestrates
as a subprocess) and serves fast search over the result — full-text, sender,
date range, and **attachment presence**, none of which plain IMAP `SEARCH` can
express.

Sync and search are deliberately one service: it already needs Maildir access
to index and serve results, so owning the sync too means one deployable unit,
one set of failure semantics, and one place that understands "is this account
actually up to date" — rather than splitting that across two systems that have
to agree with each other.

It owns the index and the Maildir it syncs into. Nothing else should write to
either.

## Running

```
uv sync
cp .env.example .env   # fill in real IMAP credentials and a real token
uv run mail-archive-server
```

Or via Docker — the image bundles `isync`:

```
docker build -t mail-archive-server .
docker run -p 4103:4103 --env-file .env \
  -v $(pwd)/data/mail:/data/mail -v $(pwd)/data/state:/data/state \
  mail-archive-server
```

On startup the Maildir is indexed (incremental — see "Indexing" below) before
the server starts accepting requests. Syncing then starts in the background —
promptly, not waiting out the first interval — and repeats every
`MAIL_ARCHIVE_SYNC_INTERVAL_SECONDS`. A slow first IMAP sync therefore never
delays the server coming up; existing local data is searchable immediately.

## Configuration

See `.env.example` for the full list. The ones that need real thought:

- `MAIL_ARCHIVE_MAILDIR_PATH` — root containing one directory per account.
  Accounts are **discovered** from this directory listing for search purposes —
  add a folder here (by any means) and it's searchable with no config change.
  Syncing is separate: only accounts with credentials under
  `MAIL_ARCHIVE_IMAP_ACCOUNTS__<name>__*` are actively synced.
- `MAIL_ARCHIVE_TOKENS__<label>__SECRET` / `__ACCOUNTS` — see "Token scoping"
  below.

## Syncing

Each configured account gets its own mbsync `Channel` and its own `SyncState`
directory (mbsync's state files are named by mailbox with no channel prefix, so
sharing one directory across accounts would corrupt sync state). `mbsyncrc` is
regenerated from config on every sync — including the account's password, so it
is written with `0600` permissions to `MAIL_ARCHIVE_MBSYNCRC_PATH`.

Each account syncs independently: **one account's failure never blocks the
others**, and its result — attempted-at timestamp, ok/failed — is recorded
directly, visible via `/status` and `/accounts`. `Expunge` is not set (defaults
to `None`), so mail deleted server-side is retained locally rather than
removed — see "Deleted-but-retained mail" below for why, and how search handles
it.

`POST /sync` triggers an immediate sync-then-reindex cycle by hand (requires a
`*`-scoped token); `POST /reindex` rescans the Maildir on disk without touching
IMAP at all, useful if something else populated it.

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

A steady-state reindex after a sync therefore touches almost nothing.

## Deleted-but-retained mail

Because sync runs with `Expunge None`, mail deleted server-side is retained
locally, flagged `T`, not removed. On the archive this design was built for,
that's ~12% of the corpus, almost entirely sitting in `INBOX` — so
`exclude_folder=Trash,Junk` alone does not filter it out. `GET /messages`
therefore excludes `T`-flagged messages by default; pass `include_deleted=true`
to see them. Every returned message carries `"deleted"` regardless.

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
- `POST /sync` and `POST /reindex` require a `*`-scoped token.

## API

```
GET  /health                 no auth
GET  /status                 index + sync freshness, per account
GET  /accounts               accounts the token can see, with counts
GET  /folders                folder counts, optionally ?account=
GET  /messages                search — see below
GET  /messages/{id}          one message including full body
POST /sync                    sync (mbsync) then reindex, all configured accounts
POST /reindex                 reindex only — no IMAP activity; ?account= for just one
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
because the code doesn't exist yet) before the implementation that turns it
green. Where a test exercises a subprocess (`mbsync`), it runs against a real
fake executable on `PATH` rather than a mock, so the actual subprocess and
argument-passing semantics are genuinely verified, including that one account
failing never stops the others. `tests/test_http.py` exercises the full
auth/scope matrix directly — a scoping bug is the one defect that would leak
mail to a caller who shouldn't see it, and it can't be caught from a client's
own tests.

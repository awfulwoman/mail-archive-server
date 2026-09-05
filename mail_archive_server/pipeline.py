from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field
from mail_archive_server.config import Config
from mail_archive_server.indexer import AccountIndexStats, reindex
from mail_archive_server.sync import SyncResult, generate_mbsyncrc, sync_all, write_mbsyncrc
from mail_archive_server.store import set_meta
from mail_archive_server.timeutil import now_utc


@dataclass
class PipelineResult:
    sync: dict[str, SyncResult] = field(default_factory=dict)
    index: dict[str, AccountIndexStats] = field(default_factory=dict)


def sync_and_reindex(conn: sqlite3.Connection, config: Config) -> PipelineResult:
    sync_results: dict[str, SyncResult] = {}

    if config.imap_accounts:
        content = generate_mbsyncrc(list(config.imap_accounts.values()), config.maildir_path)
        write_mbsyncrc(config.mbsyncrc_path, content)
        sync_results = sync_all(
            list(config.imap_accounts.values()), config.mbsync_bin, config.mbsyncrc_path
        )
        attempted_at = now_utc()
        for name, result in sync_results.items():
            set_meta(conn, f"last_sync_attempt_at:{name}", attempted_at)
            set_meta(conn, f"last_sync_ok:{name}", "1" if result.ok else "0")

    index_results = reindex(conn, config.maildir_path)
    return PipelineResult(sync=sync_results, index=index_results)

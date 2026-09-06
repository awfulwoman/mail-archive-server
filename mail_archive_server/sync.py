from __future__ import annotations
import subprocess
from dataclasses import dataclass
from pathlib import Path
from mail_archive_server.config import IMAPAccountConfig


def generate_mbsyncrc(accounts: list[IMAPAccountConfig], maildir_path: Path) -> str:
    """One Account/Store/Channel block per account, each with its OWN SyncState
    directory — mbsync's state files are named by mailbox with no channel prefix,
    so two accounts sharing one directory would corrupt each other's sync state.

    `Sync Pull`: this is an archive. mbsync only ever pulls server -> local and
    must never write to the source mailbox. Without it mbsync defaults to
    `Sync All` (bidirectional), which would push any local-only message — e.g.
    mail imported into the Maildir from another backup — back up to the live
    account. With `Sync Pull` plus `Expunge None`, local-only messages are left
    entirely alone: never pushed, never deleted."""
    blocks = []
    for acct in accounts:
        blocks.append(
            f"IMAPAccount {acct.name}\n"
            f"Host {acct.host}\n"
            f"Port {acct.port}\n"
            f"User {acct.username}\n"
            f"Pass {acct.password}\n"
            f"SSLType IMAPS\n"
            f"CertificateFile /etc/ssl/certs/ca-certificates.crt\n"
            f"\n"
            f"IMAPStore {acct.name}-remote\n"
            f"Account {acct.name}\n"
            f"\n"
            f"MaildirStore {acct.name}-local\n"
            f"Path {maildir_path}/{acct.name}/\n"
            f"Inbox {maildir_path}/{acct.name}/INBOX\n"
            f"SubFolders Verbatim\n"
            f"\n"
            f"Channel {acct.name}\n"
            f"Far :{acct.name}-remote:\n"
            f"Near :{acct.name}-local:\n"
            f"Patterns {acct.patterns}\n"
            f"Sync Pull\n"
            f"Create Near\n"
            f"Expunge None\n"
            f"SyncState {maildir_path}/.mbsync/{acct.name}/\n"
        )
    return "\n".join(blocks)


def write_mbsyncrc(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)


@dataclass(frozen=True)
class SyncResult:
    account: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_account_sync(
    account: str, mbsync_bin: str, mbsyncrc_path: Path, maildir_path: Path
) -> SyncResult:
    # mbsync's `Create Near` creates mailboxes within the store, but never the
    # store root or the SyncState directory themselves — both must already
    # exist or mbsync exits with "cannot open store". On a fresh account
    # nothing has created them yet, so do it here.
    (maildir_path / account).mkdir(parents=True, exist_ok=True)
    (maildir_path / ".mbsync" / account).mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        [mbsync_bin, "-c", str(mbsyncrc_path), "-V", account],
        capture_output=True,
        text=True,
    )
    return SyncResult(account=account, exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def sync_all(
    accounts: list[IMAPAccountConfig], mbsync_bin: str, mbsyncrc_path: Path, maildir_path: Path
) -> dict[str, SyncResult]:
    """Runs each account in turn; one account failing never stops the others."""
    return {
        acct.name: run_account_sync(acct.name, mbsync_bin, mbsyncrc_path, maildir_path)
        for acct in accounts
    }

from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Token:
    label: str
    secret: str
    accounts: frozenset[str] | None  # None means "*" — every account

    def allows(self, account: str) -> bool:
        return self.accounts is None or account in self.accounts


@dataclass(frozen=True)
class Config:
    maildir_path: Path
    db_path: Path
    host: str
    port: int
    tokens: tuple[Token, ...]
    exclude_folders_global: frozenset[str]
    exclude_folders_by_account: dict[str, frozenset[str]] = field(default_factory=dict)
    mbsync_state_path: Path = field(default=None)  # type: ignore[assignment]

    def token_for_secret(self, secret: str) -> Token | None:
        for t in self.tokens:
            if t.secret == secret:
                return t
        return None


def _parse_tokens(env: dict[str, str]) -> tuple[Token, ...]:
    prefix = "MAIL_ARCHIVE_TOKENS__"
    secrets: dict[str, str] = {}
    accounts: dict[str, str] = {}
    for key, value in env.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        if rest.endswith("__SECRET"):
            secrets[rest[: -len("__SECRET")]] = value
        elif rest.endswith("__ACCOUNTS"):
            accounts[rest[: -len("__ACCOUNTS")]] = value

    missing_secret = accounts.keys() - secrets.keys()
    if missing_secret:
        raise ConfigError(
            f"MAIL_ARCHIVE_TOKENS__<label>__ACCOUNTS set with no matching "
            f"__SECRET for label(s): {', '.join(sorted(missing_secret))}"
        )

    tokens = []
    for label, secret in secrets.items():
        raw_accounts = accounts.get(label, "*").strip()
        scope: frozenset[str] | None
        if raw_accounts in ("*", ""):
            scope = None
        else:
            scope = frozenset(a.strip() for a in raw_accounts.split(",") if a.strip())
        tokens.append(Token(label=label, secret=secret, accounts=scope))
    return tuple(tokens)


def _parse_exclude_folders(raw: str) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    global_: set[str] = set()
    by_account: dict[str, set[str]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            account, folder = entry.split(":", 1)
            by_account.setdefault(account.strip(), set()).add(folder.strip())
        else:
            global_.add(entry)
    return frozenset(global_), {k: frozenset(v) for k, v in by_account.items()}


def load_config(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)

    maildir_path = Path(env.get("MAIL_ARCHIVE_MAILDIR_PATH", "/slowpool/charlie/email"))
    db_path = Path(
        env.get("MAIL_ARCHIVE_DB_PATH", str(Path.home() / ".local/state/mail-archive-server/index.db"))
    )
    host = env.get("MAIL_ARCHIVE_HOST", "0.0.0.0")
    port = int(env.get("MAIL_ARCHIVE_PORT", "4103"))

    tokens = _parse_tokens(env)
    if not tokens:
        raise ConfigError(
            "No bearer tokens configured — set MAIL_ARCHIVE_TOKENS__<label>__SECRET "
            "(and optionally __ACCOUNTS, default '*') for at least one token."
        )

    exclude_global, exclude_by_account = _parse_exclude_folders(
        env.get("MAIL_ARCHIVE_EXCLUDE_FOLDERS", "Trash,Junk")
    )

    mbsync_state_path = Path(env.get("MAIL_ARCHIVE_MBSYNC_STATE_PATH", str(maildir_path / ".mbsync")))

    return Config(
        maildir_path=maildir_path,
        db_path=db_path,
        host=host,
        port=port,
        tokens=tokens,
        exclude_folders_global=exclude_global,
        exclude_folders_by_account=exclude_by_account,
        mbsync_state_path=mbsync_state_path,
    )

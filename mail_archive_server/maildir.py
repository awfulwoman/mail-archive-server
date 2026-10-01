from __future__ import annotations
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MaildirMessage:
    account: str
    folder: str            # relative to the account dir, e.g. "INBOX", "Trash/test"
    maildir_name: str       # filename before ":2," — stable across flag changes
    path: Path
    mtime: float
    size: int
    flags: frozenset[str]   # e.g. {"S"}, {"R", "S"}, {} for new/ (unseen)

    @property
    def seen(self) -> bool:
        return "S" in self.flags

    @property
    def deleted(self) -> bool:
        return "T" in self.flags


def discover_accounts(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def _parse_cur_filename(name: str) -> tuple[str, frozenset[str]]:
    if ":2," in name:
        base, info = name.rsplit(":2,", 1)
        return base, frozenset(info)
    return name, frozenset()


def _iter_mail_folders(account_root: Path) -> Iterator[tuple[str, Path]]:
    for dirpath, dirnames, _filenames in os.walk(account_root):
        dirnames[:] = [d for d in dirnames if d != "tmp" and not d.startswith(".")]
        p = Path(dirpath)
        if "cur" in dirnames and "new" in dirnames:
            rel = p.relative_to(account_root)
            folder = "" if str(rel) == "." else str(rel)
            yield folder, p
        dirnames[:] = [d for d in dirnames if d not in ("cur", "new")]


def _iter_folder_messages(folder_path: Path) -> Iterator[tuple[str, Path, frozenset[str], float, int]]:
    for sub in ("new", "cur"):
        d = folder_path / sub
        if not d.is_dir():
            continue
        for entry in d.iterdir():
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if sub == "new":
                maildir_name, flags = entry.name, frozenset()
            else:
                maildir_name, flags = _parse_cur_filename(entry.name)
            st = entry.stat()
            yield maildir_name, entry, flags, st.st_mtime, st.st_size


def set_flags(path: Path, flags: frozenset[str]) -> Path:
    """Renames a message file to carry exactly `flags`, Maildir's own
    mechanism for flag changes (mail-archive-server#1's mark-read: the
    service writes to its own Maildir directly, the same way mbsync
    itself already does -- no raw IMAP STORE code needed). Any flag at
    all means the message belongs in cur/, never new/ ("unseen by any
    client") -- moved there if it's still in new/.
    """
    base, _ = _parse_cur_filename(path.name)
    folder_dir = path.parent.parent
    dest_dir = folder_dir / ("cur" if flags else path.parent.name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    new_path = dest_dir / f"{base}:2,{''.join(sorted(flags))}"
    path.rename(new_path)
    return new_path


def walk_account(account_root: Path, account: str) -> Iterator[MaildirMessage]:
    for folder, folder_path in _iter_mail_folders(account_root):
        for maildir_name, path, flags, mtime, size in _iter_folder_messages(folder_path):
            yield MaildirMessage(
                account=account,
                folder=folder,
                maildir_name=maildir_name,
                path=path,
                mtime=mtime,
                size=size,
                flags=flags,
            )

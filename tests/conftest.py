from __future__ import annotations
from pathlib import Path
import pytest

FAKE_MBSYNC_SCRIPT = """#!/bin/bash
# Fake mbsync for tests: args are "-c <config> -V <channel>".
config="$2"
channel="$4"
marker_dir="$(dirname "$config")/fake_mbsync_markers"
mkdir -p "$marker_dir"
echo "$channel" >> "$marker_dir/invoked.log"
if [[ -f "$marker_dir/$channel.fail" ]]; then
  echo "fake mbsync: simulated failure for $channel" >&2
  exit 1
fi
if [[ -f "$marker_dir/../maildir_path.txt" ]]; then
  maildir="$(cat "$marker_dir/../maildir_path.txt")"
  mkdir -p "$maildir/$channel/INBOX/cur" "$maildir/$channel/INBOX/new" "$maildir/$channel/INBOX/tmp"
  printf 'From: a@b.com\\r\\nSubject: Synced %s\\r\\n\\r\\nbody' "$channel" \\
    > "$maildir/$channel/INBOX/cur/1.uniq:2,S"
fi
echo "fake mbsync: synced $channel"
exit 0
"""


def install_fake_mbsync(tmp_path: Path) -> Path:
    bin_path = tmp_path / "fake-mbsync"
    bin_path.write_text(FAKE_MBSYNC_SCRIPT)
    bin_path.chmod(0o755)
    return bin_path


@pytest.fixture
def fake_mbsync_bin(tmp_path: Path) -> Path:
    return install_fake_mbsync(tmp_path)

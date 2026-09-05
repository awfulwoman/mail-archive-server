from __future__ import annotations
import pytest
from mail_archive_server.config import ConfigError, load_config


def _env(**overrides):
    base = {"MAIL_ARCHIVE_TOKENS__gateway__SECRET": "secret1"}
    base.update(overrides)
    return base


def test_defaults():
    cfg = load_config(_env())
    assert str(cfg.maildir_path) == "/slowpool/charlie/email"
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 4103
    assert cfg.exclude_folders_global == frozenset({"Trash", "Junk"})
    assert cfg.exclude_folders_by_account == {}


def test_overrides():
    cfg = load_config(_env(
        MAIL_ARCHIVE_MAILDIR_PATH="/tmp/mail",
        MAIL_ARCHIVE_HOST="127.0.0.1",
        MAIL_ARCHIVE_PORT="9999",
    ))
    assert str(cfg.maildir_path) == "/tmp/mail"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 9999


def test_no_tokens_raises():
    with pytest.raises(ConfigError):
        load_config({})


def test_single_token_defaults_to_wildcard_scope():
    cfg = load_config(_env())
    assert len(cfg.tokens) == 1
    token = cfg.tokens[0]
    assert token.label == "gateway"
    assert token.secret == "secret1"
    assert token.accounts is None
    assert token.allows("personal")
    assert token.allows("anything")


def test_token_with_explicit_wildcard():
    cfg = load_config(_env(MAIL_ARCHIVE_TOKENS__gateway__ACCOUNTS="*"))
    assert cfg.tokens[0].accounts is None


def test_token_scoped_to_csv_accounts():
    cfg = load_config(_env(MAIL_ARCHIVE_TOKENS__gateway__ACCOUNTS="personal, work"))
    token = cfg.tokens[0]
    assert token.accounts == frozenset({"personal", "work"})
    assert token.allows("personal")
    assert token.allows("work")
    assert not token.allows("other")


def test_multiple_labelled_tokens():
    cfg = load_config({
        "MAIL_ARCHIVE_TOKENS__gateway__SECRET": "s1",
        "MAIL_ARCHIVE_TOKENS__gateway__ACCOUNTS": "*",
        "MAIL_ARCHIVE_TOKENS__scratch__SECRET": "s2",
        "MAIL_ARCHIVE_TOKENS__scratch__ACCOUNTS": "personal",
    })
    by_label = {t.label: t for t in cfg.tokens}
    assert by_label["gateway"].accounts is None
    assert by_label["scratch"].accounts == frozenset({"personal"})


def test_accounts_with_no_matching_secret_raises():
    with pytest.raises(ConfigError):
        load_config({
            "MAIL_ARCHIVE_TOKENS__gateway__SECRET": "s1",
            "MAIL_ARCHIVE_TOKENS__orphan__ACCOUNTS": "personal",
        })


def test_token_for_secret_lookup():
    cfg = load_config(_env())
    assert cfg.token_for_secret("secret1") is not None
    assert cfg.token_for_secret("wrong") is None


def test_exclude_folders_bare_names_apply_globally():
    cfg = load_config(_env(MAIL_ARCHIVE_EXCLUDE_FOLDERS="Trash,Spam"))
    assert cfg.exclude_folders_global == frozenset({"Trash", "Spam"})
    assert cfg.exclude_folders_by_account == {}


def test_exclude_folders_account_scoped():
    cfg = load_config(_env(
        MAIL_ARCHIVE_EXCLUDE_FOLDERS="Trash,work:[Gmail]/Trash,work:[Gmail]/All Mail"
    ))
    assert cfg.exclude_folders_global == frozenset({"Trash"})
    assert cfg.exclude_folders_by_account == {
        "work": frozenset({"[Gmail]/Trash", "[Gmail]/All Mail"})
    }


def test_mbsync_state_path_defaults_under_maildir():
    cfg = load_config(_env(MAIL_ARCHIVE_MAILDIR_PATH="/tmp/mail"))
    assert str(cfg.mbsync_state_path) == "/tmp/mail/.mbsync"


def test_mbsync_state_path_override():
    cfg = load_config(_env(MAIL_ARCHIVE_MBSYNC_STATE_PATH="/tmp/state"))
    assert str(cfg.mbsync_state_path) == "/tmp/state"

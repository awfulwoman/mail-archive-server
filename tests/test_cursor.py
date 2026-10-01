from __future__ import annotations
import pytest
from mail_archive_server.cursor import InvalidCursor, decode, encode
from mail_archive_server.store import SearchFilters


def make_filters(**overrides):
    base = dict(accounts=["personal"], order="date_asc")
    base.update(overrides)
    return SearchFilters(**base)


def test_a_cursor_round_trips_to_the_same_position():
    filters = make_filters()
    token = encode(date_utc="2026-09-01T00:00:00Z", id="abc123", filters=filters)

    date_utc, id_ = decode(token, filters=filters)

    assert (date_utc, id_) == ("2026-09-01T00:00:00Z", "abc123")


def test_a_malformed_token_raises_invalid_cursor():
    filters = make_filters()

    with pytest.raises(InvalidCursor):
        decode("not-a-real-cursor", filters=filters)


def test_a_cursor_issued_under_different_filters_is_rejected():
    issued_under = make_filters(accounts=["personal"])
    token = encode(date_utc="2026-09-01T00:00:00Z", id="abc123", filters=issued_under)

    replayed_under = make_filters(accounts=["work"])

    with pytest.raises(InvalidCursor):
        decode(token, filters=replayed_under)


def test_a_cursor_issued_under_a_different_order_is_rejected():
    issued_under = make_filters(order="date_asc")
    token = encode(date_utc="2026-09-01T00:00:00Z", id="abc123", filters=issued_under)

    replayed_under = make_filters(order="date_desc")

    with pytest.raises(InvalidCursor):
        decode(token, filters=replayed_under)


def test_limit_offset_and_after_do_not_affect_the_fingerprint():
    # These vary page to page within the same walk -- a cursor issued on
    # page 1 must still decode on page 2, where offset/after differ.
    issued_under = make_filters(limit=10, offset=0, after=None)
    token = encode(date_utc="2026-09-01T00:00:00Z", id="abc123", filters=issued_under)

    replayed_under = make_filters(limit=25, offset=5, after=("2026-08-01T00:00:00Z", "zzz"))

    date_utc, id_ = decode(token, filters=replayed_under)
    assert (date_utc, id_) == ("2026-09-01T00:00:00Z", "abc123")


def test_a_cursor_issued_with_an_exclude_account_folder_pair_set_round_trips():
    # exclude_account_folder_pairs is a set of tuples -- not natively
    # JSON-serialisable, and set iteration order isn't guaranteed; the
    # fingerprint must still be stable and comparable.
    filters = make_filters(exclude_account_folder_pairs={("personal", "Trash"), ("work", "Spam")})
    token = encode(date_utc="2026-09-01T00:00:00Z", id="abc123", filters=filters)

    same_set_different_construction_order = make_filters(
        exclude_account_folder_pairs={("work", "Spam"), ("personal", "Trash")}
    )

    date_utc, id_ = decode(token, filters=same_set_different_construction_order)
    assert (date_utc, id_) == ("2026-09-01T00:00:00Z", "abc123")

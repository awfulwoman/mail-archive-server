from __future__ import annotations
from mail_archive_server.parser import parse_message


def _msg(headers: str, body: str) -> bytes:
    return (headers.replace("\n", "\r\n") + "\r\n\r\n" + body).encode("utf-8")


def test_basic_headers_and_plain_body():
    raw = _msg(
        "From: Alice Smith <alice@example.com>\n"
        "To: charlie@example.com\n"
        "Subject: Hello there\n"
        "Message-ID: <abc123@example.com>\n"
        "Date: Tue, 01 Sep 2026 09:15:04 +0000",
        "Hi Charlie, just checking in.",
    )
    m = parse_message(raw)
    assert m.message_id == "<abc123@example.com>"
    assert m.from_name == "Alice Smith"
    assert m.from_addr == "alice@example.com"
    assert m.from_raw == "Alice Smith <alice@example.com>"
    assert m.to_raw == "charlie@example.com"
    assert m.subject == "Hello there"
    assert m.date_utc == "2026-09-01T09:15:04Z"
    assert m.body == "Hi Charlie, just checking in."
    assert m.has_attachment is False
    assert m.attachments == []


def test_date_converted_to_utc_from_offset():
    raw = _msg(
        "From: a@b.com\nSubject: x\nDate: Tue, 01 Sep 2026 09:15:04 +0200",
        "body",
    )
    m = parse_message(raw)
    assert m.date_utc == "2026-09-01T07:15:04Z"


def test_missing_date_header_yields_none():
    raw = _msg("From: a@b.com\nSubject: x", "body")
    m = parse_message(raw)
    assert m.date_utc is None


def test_malformed_date_header_yields_none():
    raw = _msg("From: a@b.com\nSubject: x\nDate: not-a-date", "body")
    m = parse_message(raw)
    assert m.date_utc is None


def test_rfc2047_encoded_subject_and_from_name_decoded():
    raw = _msg(
        "From: =?UTF-8?B?Q2hhcmxpZSDDlidIYXJh?= <charlie@example.com>\n"
        "Subject: =?UTF-8?B?VMOpc3Qgc3ViamVjdA==?=",
        "body",
    )
    m = parse_message(raw)
    assert m.from_name == "Charlie Ö'Hara"
    assert m.from_addr == "charlie@example.com"
    assert m.subject == "Tést subject"


def test_missing_message_id_is_empty_string():
    raw = _msg("From: a@b.com\nSubject: x", "body")
    m = parse_message(raw)
    assert m.message_id == ""


def test_multipart_alternative_prefers_plain_over_html():
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: x\r\n"
        b'Content-Type: multipart/alternative; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"plain body\r\n"
        b"--B\r\n"
        b"Content-Type: text/html\r\n\r\n"
        b"<p>html body</p>\r\n"
        b"--B--\r\n"
    )
    m = parse_message(raw)
    assert m.body == "plain body"
    assert m.has_attachment is False


def test_html_only_body_used_when_no_plain_part():
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: x\r\n"
        b'Content-Type: multipart/alternative; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/html\r\n\r\n"
        b"<p>html only</p>\r\n"
        b"--B--\r\n"
    )
    m = parse_message(raw)
    assert m.body == "<p>html only</p>"


def test_attachment_via_content_disposition():
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: x\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"see attached\r\n"
        b"--B\r\n"
        b"Content-Type: application/pdf\r\n"
        b'Content-Disposition: attachment; filename="invoice.pdf"\r\n\r\n'
        b"%PDF-fake-bytes\r\n"
        b"--B--\r\n"
    )
    m = parse_message(raw)
    assert m.body == "see attached"
    assert m.has_attachment is True
    assert len(m.attachments) == 1
    att = m.attachments[0]
    assert att["filename"] == "invoice.pdf"
    assert att["content_type"] == "application/pdf"
    assert att["content_disposition"] == "attachment"
    assert att["size_bytes"] == len(b"%PDF-fake-bytes")


def test_attachment_via_filename_without_explicit_disposition():
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: x\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"body\r\n"
        b"--B\r\n"
        b'Content-Type: application/octet-stream; name="data.bin"\r\n\r\n'
        b"binarydata\r\n"
        b"--B--\r\n"
    )
    m = parse_message(raw)
    assert m.has_attachment is True
    assert m.attachments[0]["filename"] == "data.bin"
    assert m.attachments[0]["content_disposition"] is None


def test_inline_text_plain_with_no_filename_is_not_an_attachment():
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: x\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"just a body, no filename\r\n"
        b"--B--\r\n"
    )
    m = parse_message(raw)
    assert m.has_attachment is False
    assert m.attachments == []


def test_multipart_container_itself_never_counted_as_attachment():
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: x\r\n"
        b'Content-Type: multipart/mixed; boundary="OUTER"\r\n'
        b"\r\n"
        b"--OUTER\r\n"
        b'Content-Type: multipart/alternative; boundary="INNER"\r\n'
        b"\r\n"
        b"--INNER\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"body text\r\n"
        b"--INNER--\r\n"
        b"--OUTER\r\n"
        b"Content-Type: application/zip\r\n"
        b'Content-Disposition: attachment; filename="archive.zip"\r\n\r\n'
        b"zipbytes\r\n"
        b"--OUTER--\r\n"
    )
    m = parse_message(raw)
    assert m.body == "body text"
    assert len(m.attachments) == 1
    assert m.attachments[0]["filename"] == "archive.zip"


def test_embedded_message_rfc822_always_counts_as_attachment():
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: x\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"fwd message below\r\n"
        b"--B\r\n"
        b"Content-Type: message/rfc822\r\n\r\n"
        b"From: nested@example.com\r\n"
        b"Subject: inner\r\n"
        b'Content-Type: multipart/mixed; boundary="NESTED"\r\n'
        b"\r\n"
        b"--NESTED\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"inner body\r\n"
        b"--NESTED\r\n"
        b"Content-Type: application/pdf\r\n"
        b'Content-Disposition: attachment; filename="inner.pdf"\r\n\r\n'
        b"pdfbytes\r\n"
        b"--NESTED--\r\n"
        b"--B--\r\n"
    )
    m = parse_message(raw)
    assert m.has_attachment is True
    # exactly one attachment for the embedded message — its own internal
    # multipart structure and inner attachment must NOT be flattened out
    assert len(m.attachments) == 1
    assert m.attachments[0]["content_type"] == "message/rfc822"


def test_rfc2047_encoded_attachment_filename_decoded():
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: x\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"body\r\n"
        b"--B\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=\"=?UTF-8?B?ZsO8bnVuZQ==?=.pdf\"\r\n\r\n"
        b"pdfbytes\r\n"
        b"--B--\r\n"
    )
    m = parse_message(raw)
    assert m.attachments[0]["filename"] == "fünune.pdf"


def test_no_body_parts_yields_empty_body():
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: x\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: application/pdf\r\n"
        b'Content-Disposition: attachment; filename="only.pdf"\r\n\r\n'
        b"pdfbytes\r\n"
        b"--B--\r\n"
    )
    m = parse_message(raw)
    assert m.body == ""
    assert m.has_attachment is True

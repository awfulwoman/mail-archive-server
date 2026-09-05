from __future__ import annotations
import email as email_lib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timezone
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime


@dataclass(frozen=True)
class ParsedMessage:
    message_id: str
    from_raw: str
    from_name: str
    from_addr: str
    to_raw: str
    cc_raw: str
    subject: str
    date_utc: str | None
    body: str
    has_attachment: bool
    attachments: list[dict] = field(default_factory=list)


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    out = []
    for text, enc in decode_header(raw):
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _iter_leaf_parts(msg: Message) -> Iterator[Message]:
    """Yields every non-multipart part. A message/rfc822 part is a leaf — it is
    one attachment, not a source of further top-level attachments from its own
    internal structure."""
    if msg.get_content_maintype() == "multipart":
        for sub in msg.get_payload():
            if isinstance(sub, Message):
                yield from _iter_leaf_parts(sub)
    else:
        yield msg


def _is_attachment(part: Message) -> bool:
    ctype = part.get_content_type()
    if ctype == "message/rfc822":
        return True
    if part.get_content_disposition() == "attachment":
        return True
    if part.get_filename() is not None and ctype not in ("text/plain", "text/html"):
        return True
    return False


def _decode_text_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _extract_body(msg: Message) -> str:
    plain: str | None = None
    html: str | None = None
    for part in _iter_leaf_parts(msg):
        if _is_attachment(part):
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = _decode_text_payload(part)
        elif ctype == "text/html" and html is None:
            html = _decode_text_payload(part)
    return plain if plain else (html if html else "")


def _extract_attachments(msg: Message) -> list[dict]:
    attachments = []
    for part in _iter_leaf_parts(msg):
        if not _is_attachment(part):
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        attachments.append({
            "filename": _decode_header_value(filename) if filename else None,
            "content_type": part.get_content_type(),
            "size_bytes": len(payload) if payload is not None else None,
            "content_disposition": part.get_content_disposition(),
        })
    return attachments


def _extract_date_utc(msg: Message) -> str | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_message(raw: bytes) -> ParsedMessage:
    msg = email_lib.message_from_bytes(raw)

    from_header = msg.get("From", "") or ""
    realname_raw, addr = parseaddr(from_header)

    attachments = _extract_attachments(msg)

    return ParsedMessage(
        message_id=(msg.get("Message-ID", "") or "").strip(),
        from_raw=_decode_header_value(from_header),
        from_name=_decode_header_value(realname_raw),
        from_addr=addr,
        to_raw=_decode_header_value(msg.get("To", "")),
        cc_raw=_decode_header_value(msg.get("Cc", "")),
        subject=_decode_header_value(msg.get("Subject", "")),
        date_utc=_extract_date_utc(msg),
        body=_extract_body(msg),
        has_attachment=bool(attachments),
        attachments=attachments,
    )

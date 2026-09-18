
# (offset, bytes) signatures mapped to (mime, extension). Detected from the
# first few KB of the file rather than trusting the client-supplied Content-Type.
# MVP: validate these well-known formats; unknown binaries are accepted as
# application/octet-stream (the plan requires magic-byte checks "for at least
# one format").
_MAGIC: list[tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"%PDF-", "application/pdf", "pdf"),
    (b"\x1f\x8b", "application/gzip", "gz"),
    (b"PK\x03\x04", "application/zip", "zip"),
    (b"PK\x05\x06", "application/zip", "zip"),
    (b"PK\x07\x08", "application/zip", "zip"),
    (b"Rar!\x1a\x07", "application/vnd.rar", "rar"),
    (b"\x42\x5a\x68", "application/x-bzip2", "bz2"),
    (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed", "7z"),
    (b"\x00\x00\x00\x18ftyp", "video/mp4", "mp4"),
    (b"\x00\x00\x00 ftyp", "video/mp4", "mp4"),
    (b"\x1a\x45\xdf\xa3", "video/webm", "webm"),
    (b"ID3", "audio/mpeg", "mp3"),
    (b"\xff\xfb", "audio/mpeg", "mp3"),
    (b"\xff\xf3", "audio/mpeg", "mp3"),
    (b"{\\rtf", "application/rtf", "rtf"),
    (b"\x25\x21", "application/postscript", "ps"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-msi", "msi"),
]

_TEXT_PREFIXES = (b"\xef\xbb\xbf", b"<!DOCTYPE html", b"<html", b"<?xml", b"#!", b"/*", b"//")


def sniff(prefix: bytes) -> tuple[str, str] | None:
    """Inspect magic bytes. Returns (mime, extension) or None if unrecognized."""
    if prefix.startswith((b"PK\x03\x04",)):
        # Distinguish docx/xlsx/pptx from plain zip via docProps
        if any(s in prefix[:4096] for s in (b"[Content_Types].xml", b"word/", b"xl/", b"ppt/")):
            return ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")
        return ("application/zip", "zip")
    for sig, mime, ext in _MAGIC:
        if prefix.startswith(sig):
            return (mime, ext)
    return None


def is_plain_text(prefix: bytes) -> bool:
    """Try declaring text only to avoid binary trickery; used to refine octet-stream."""
    if not prefix:
        return False
    try:
        prefix.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return False
    if b"\x00" in prefix:
        return False
    return True


def normalize_content_type(prefix: bytes, client_mime: str | None) -> tuple[str, str | None]:
    """Return (mime, ext). Falls back to client-provided mime for text, else octet-stream."""
    found = sniff(prefix)
    if found:
        mime, ext = found
        if ext == "zip":
            return mime, ext
        return mime, ext
    if is_plain_text(prefix):
        return (client_mime or "text/plain"), None
    return "application/octet-stream", None
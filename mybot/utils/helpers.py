from pathlib import Path


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes (first 16 bytes)."""
    if not data:
        return None
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # JPEG: FF D8 FF
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    # GIF: GIF87a or GIF89a
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    # WebP: RIFF....WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # BMP: BM
    if data[:2] == b"BM":
        return "image/bmp"
    # TIFF: II (little-endian) or MM (big-endian)
    if data[:2] in (b"II", b"MM"):
        return "image/tiff"
    return None

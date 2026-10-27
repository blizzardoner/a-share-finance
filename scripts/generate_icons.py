from __future__ import annotations

import struct
import zlib
from pathlib import Path


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def write_icon(size: int, path: Path) -> None:
    pixels = bytearray()
    bars = [(0.27, 0.60), (0.43, 0.42), (0.59, 0.25), (0.75, 0.12)]
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            radial = max(0.0, 1.0 - (((x-size*.72)**2 + (y-size*.18)**2) ** .5) / (size*.85))
            r = int(8 + 48 * radial)
            g = int(13 + 33 * radial)
            b = int(25 + 92 * radial)
            for cx, top in bars:
                half = size * .045
                if abs(x - size * cx) <= half and size * top <= y <= size * .74:
                    t = (y - size * top) / max(1, size * (.74 - top))
                    r = int(78 + 62 * t)
                    g = int(215 - 100 * t)
                    b = int(221 + 25 * t)
            # baseline
            if size * .78 <= y <= size * .795 and size * .19 <= x <= size * .82:
                r, g, b = 139, 114, 255
            pixels.extend((r, g, b, 255))
    raw = b"\x89PNG\r\n\x1a\n"
    raw += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(bytes(pixels), 9))
    raw += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "public" / "icons"
    write_icon(180, root / "apple-touch-icon.png")
    write_icon(192, root / "icon-192.png")
    write_icon(512, root / "icon-512.png")

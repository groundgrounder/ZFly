"""Locate application-owned icons without relying on desktop theme caches."""
from pathlib import Path


def icon_file(size=256):
    root = Path(__file__).resolve().parents[2]
    for path in (root / f"icons/hicolor/{size}x{size}/apps/io.github.zfly.ZFly.png",
                 root / f"assets/icons/zfly-{size}.png"):
        if path.is_file():
            return path
    return None

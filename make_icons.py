#!/usr/bin/env python3
"""Kategori ikonlarini e-posta icin PNG'ye cevirir.

Neden PNG: e-posta istemcileri (basta Gmail) satir ici SVG'yi de `data:` URI
gorsellerini de atar. Tek saglam yol, mutlak https adresinden servis edilen
raster gorsel -- ikonlar `output/icons/` altina yazilir ve zaten her kosuda
GitHub Pages'e yayinlanir.

Cevirici olarak headless Chrome kullanilir (ek bagimlilik yok).
Calistir:  .venv/bin/python make_icons.py
"""
import subprocess
import tempfile
from pathlib import Path

from digest import _ICON_BODY, EMAIL_ICON_COLOR

CHROME = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
OUT_DIR = Path(__file__).parent / "output" / "icons"
SIZE = 96  # 4x: e-postada 24px gosterilir, retina ekranlarda net kalsin


def render(name: str, body: str) -> Path:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" '
           f'width="{SIZE}" height="{SIZE}" fill="none" stroke="{EMAIL_ICON_COLOR}" '
           f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
           f'{body.replace("currentColor", EMAIL_ICON_COLOR)}</svg>')
    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
            f'html,body{{margin:0;padding:0;background:transparent}}</style></head>'
            f'<body>{svg}</body></html>')
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                     encoding="utf-8") as f:
        f.write(html)
        tmp = f.name
    out = OUT_DIR / f"{name}.png"
    subprocess.run([
        CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
        "--default-background-color=00000000",
        f"--window-size={SIZE},{SIZE}", f"--screenshot={out}", f"file://{tmp}",
    ], check=True, capture_output=True)
    Path(tmp).unlink(missing_ok=True)
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in _ICON_BODY.items():
        path = render(name, body)
        print(f"  {path.relative_to(Path(__file__).parent)}  "
              f"({path.stat().st_size} bayt)")
    print(f"\n{len(_ICON_BODY)} ikon yazildi -> {OUT_DIR}")


if __name__ == "__main__":
    main()

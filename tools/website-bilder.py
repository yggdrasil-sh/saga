"""Bilder der Projektseite (docs/index.html und docs/<sprache>/index.html) aus den Aufnahmen w-*.png machen.

  python3 tools/website-bilder.py <ordner-mit-w-png> [docs] [--poster]

Aufnahmen: tools/handbuch-stack.sh --nur w-   (Szenen w-* in tools/screens-handbuch.py, Ergebnis in $HB_OUT).
Ergebnis: docs/bilder/<name>.webp (Desktop hoechstens 2000 px breit, Handy 780 px, WebP Qualitaet 80). Nur mit --poster
zusaetzlich ein Poster docs/video/saga-promo.jpg (1920x1080, aus w-heute-dunkel, JPEG <= 300 KB) - ohne den Schalter bleibt
das Poster aus dem Promovideo unangetastet. Braucht nur Pillow mit WebP."""
import glob
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    args = [a for a in sys.argv[1:] if a != "--poster"]
    want_poster = "--poster" in sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    src = args[0]
    docs = args[1] if len(args) > 1 else os.path.join(os.path.dirname(HERE), "docs")
    out = os.path.join(docs, "bilder")
    os.makedirs(out, exist_ok=True)
    total = 0
    for p in sorted(glob.glob(os.path.join(src, "w-*.png"))):
        name = os.path.basename(p)[2:-4]                 # w-heute-hell.png -> heute-hell
        im = Image.open(p).convert("RGB")
        maxw = 780 if name.startswith("handy-") else 2000
        if im.width > maxw:
            im = im.resize((maxw, round(im.height * maxw / im.width)), Image.LANCZOS)
        dst = os.path.join(out, name + ".webp")
        im.save(dst, "WEBP", quality=80, method=6)
        size = os.path.getsize(dst)
        total += size
        print(f"{dst}  {im.width}x{im.height}  {size // 1024} KB")
    poster = os.path.join(src, "w-heute-dunkel.png")
    if want_poster and os.path.exists(poster):
        im = Image.open(poster).convert("RGB")
        im = im.resize((1920, round(im.height * 1920 / im.width)), Image.LANCZOS)
        im = im.crop((0, 0, 1920, 1080))
        os.makedirs(os.path.join(docs, "video"), exist_ok=True)
        dst = os.path.join(docs, "video", "saga-promo.jpg")
        for q in (85, 80, 75, 70, 65, 60):
            im.save(dst, "JPEG", quality=q, optimize=True, progressive=True)
            if os.path.getsize(dst) <= 300 * 1024:
                break
        print(f"{dst}  1920x1080  {os.path.getsize(dst) // 1024} KB (Qualitaet {q})")
        total += os.path.getsize(dst)
    print(f"gesamt {total / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""PNG-Symbole aus static/icon.svg erzeugen (Playwright/Chromium): icon-192, icon-512, apple-touch-icon.
Aufruf im Repo-Verzeichnis: python tools/render-icons.py"""
import os
from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")
svg = open(os.path.join(ROOT, "icon.svg"), encoding="utf-8").read()
with sync_playwright() as p:
    b = p.chromium.launch()
    for size, name in ((512, "icon-512.png"), (192, "icon-192.png"), (180, "apple-touch-icon.png")):
        pg = b.new_page(viewport={"width": size, "height": size})
        pg.set_content('<html><body style="margin:0;background:transparent">'
                       + svg.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1) + "</body></html>")
        pg.screenshot(path=os.path.join(ROOT, name), omit_background=True)
        print("icon", name)
    b.close()

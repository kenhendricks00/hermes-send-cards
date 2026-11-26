"""Rebuild the icon PNGs, the X example image and the README banner with LOCAL headless Edge.

  PYTHONPATH= uv run --no-project --with playwright python tests/make_assets.py
"""
import importlib.util
import json
from pathlib import Path
import tempfile
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('draft_card', ROOT / 'skills/send-cards/scripts/draft_card.py')
card = importlib.util.module_from_spec(spec)
spec.loader.exec_module(card)

THEME = (':root{--foreground:#ececee;--muted-foreground:#9a9ca3;--border:#34363c;--card:#1b1c20;color-scheme:dark}'
         "body{margin:0;font-family:'Segoe UI',Inter,Helvetica,Arial,sans-serif;color:var(--foreground)}")

X_PREVIEW = """<!doctype html><meta charset="utf-8"><style>
body{margin:0;width:1200px;height:630px;display:grid;place-items:center;font-family:'Segoe UI',Inter,Arial,sans-serif;
background:linear-gradient(135deg,#1d2433,#0f1218);color:#e8eaef}
.w{display:flex;align-items:center;gap:60px}.t b{display:block;font-size:64px;font-weight:700}
.t span{font-size:30px;color:#9aa1ae}img{width:220px;height:220px}</style>
<div class="w"><img src="ICON"><div class="t"><b>Nothing sends</b><span>until you tap Send.</span></div></div>"""

BANNER = """<!doctype html><meta charset="utf-8"><style>THEME
html,body{width:1200px;height:600px;overflow:hidden}
body{background:radial-gradient(900px 500px at 100% 0,#1b2230,transparent),#0f1013;display:flex;align-items:center;padding:0 80px;gap:56px;box-sizing:border-box}
.l{flex:1}.l img{width:120px;height:120px}
h1{font-size:64px;margin:46px 0 14px;font-weight:700;color:#f3f4f6;letter-spacing:-.5px}
p{font-size:26px;line-height:1.4;color:#9aa1ae;margin:0;max-width:520px}
.r{width:400px;flex:none}</style>
<div class="l"><img src="ICON"><h1>Send Cards</h1><p>Read every message your Hermes agent wants to send, then tap Send.</p></div>
<div class="r">CARD</div>"""

BANNER_DRAFT = {
    'draft_id': 'em-banner', 'channel': 'email', 'from': 'you@example.com',
    'to': [{'name': 'Sam', 'address': 'sam@example.com'}], 'subject': 'Friday plans',
    'body': 'Hi Sam,\n\nFriday at 7 still works for me. I will bring the board games.\n\nSee you then,\nAlex',
}


def main():
    assets, docs = ROOT / 'assets', ROOT / 'docs'
    docs.mkdir(exist_ok=True)
    icon = (assets / 'icon.svg').resolve().as_uri()
    with tempfile.TemporaryDirectory(prefix='dc-assets-') as temp, sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        ctx = browser.new_context(device_scale_factor=1)
        ctx.route('**/*', lambda r: r.continue_() if r.request.url.startswith(('file://', 'data:')) else r.abort())
        page = ctx.new_page()
        for size in (512, 256, 128, 64):
            page.set_viewport_size({'width': size, 'height': size})
            page.set_content(f'<style>body{{margin:0}}img{{display:block}}</style><img src="{icon}" width="{size}" height="{size}">')
            page.wait_for_timeout(100)
            page.screenshot(path=str(assets / f'icon-{size}.png'), omit_background=True)

        def shot(markup, w, h, out):
            f = Path(temp) / 'page.html'
            f.write_text(markup, encoding='utf-8')
            page.set_viewport_size({'width': w, 'height': h})
            page.goto(f.as_uri())
            page.wait_for_timeout(300)
            page.screenshot(path=str(out))

        shot(X_PREVIEW.replace('ICON', icon), 1200, 630, ROOT / 'skills/send-cards/examples/x-preview.png')
        banner_card = card.render(BANNER_DRAFT)
        shot(BANNER.replace('THEME', THEME).replace('ICON', icon).replace('CARD', banner_card), 1200, 600, docs / 'banner.png')
        browser.close()
    print('Wrote assets/icon-{512,256,128,64}.png, skills/send-cards/examples/x-preview.png, docs/banner.png')


if __name__ == '__main__':
    main()

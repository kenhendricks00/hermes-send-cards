"""Optional LOCAL Playwright check and README screenshots. No remote browser, no network.

  PYTHONPATH= uv run --no-project --with playwright python tests/browser_check.py

Each card is wrapped in a small host page that stands in for Hermes: theme variables and a stubbed
window.hermes.send that records prompts (data-hermes-send clicks go through the same stub).
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'skills/send-cards/examples'
spec = importlib.util.spec_from_file_location('draft_card', ROOT / 'skills/send-cards/scripts/draft_card.py')
card = importlib.util.module_from_spec(spec)
spec.loader.exec_module(card)

DARK = '--foreground:#ececee;--muted-foreground:#9a9ca3;--border:#34363c;--card:#1b1c20;color-scheme:dark'
NO_CARD = '--foreground:#f2f2f2;--muted-foreground:#9a9ca3;--border:#34363c'  # host without --card

HOST = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>:root{THEME}html{background:BG}body{margin:0;padding:16px;font-family:'Segoe UI',Inter,Helvetica,Arial,sans-serif;color:var(--foreground,#16171a)}</style>
<script>window.__sent=[];window.__ack={ok:true};
window.hermes={maxLength:500,send:function(p){window.__sent.push(p);return Promise.resolve(window.__ack)}};
addEventListener('click',function(e){var el=e.target.closest&&e.target.closest('[data-hermes-send]');
if(el)window.hermes.send(el.getAttribute('data-hermes-send'))},true);</script></head><body>CARD</body></html>"""


def example(name):
    return json.loads((EXAMPLES / f'{name}.json').read_text(encoding='utf-8'))


def main():
    docs = ROOT / 'docs'
    docs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dc-browser-') as temp:
        site = Path(temp)

        def write(name, o, state='pending', theme=DARK, bg='#141518'):
            page = card.render(o, state, str(EXAMPLES))
            (site / f'{name}.html').write_text(HOST.replace('THEME', theme).replace('BG', bg).replace('CARD', page), encoding='utf-8')
            return name

        for n in ('email', 'discord', 'linkedin', 'x'):
            write(n, example(n))
            write(f'{n}-sent', example(n), 'sent')
            write(f'{n}-discarded', example(n), 'discarded')
        write('github', example('github'))
        write('github-sent', example('github'), 'sent')
        write('email-nocard', example('email'), theme=NO_CARD)
        write('x-nocard', example('x'), theme=NO_CARD)
        write('email-plain', example('email'), theme='', bg='#ffffff')

        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(site)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}/'
        blocked, errors, passed = [], [], []

        def route(r):
            if r.request.url.startswith(('http://127.0.0.1:', 'data:')):
                r.continue_()
            else:
                blocked.append(r.request.url)
                r.abort()

        try:
            with sync_playwright() as pw:
                # Existing local Edge only. Never download a browser implicitly.
                browser = pw.chromium.launch(channel='msedge', headless=True)

                def open_page(name, width=640, height=900):
                    ctx = browser.new_context(viewport={'width': width, 'height': height}, device_scale_factor=2)
                    ctx.route('**/*', route)
                    pg = ctx.new_page()
                    pg.on('pageerror', lambda e: errors.append(f'{name}: {e}'))
                    pg.goto(base + name + '.html')
                    pg.wait_for_load_state('networkidle')
                    pg.wait_for_timeout(350)
                    return pg

                def snap(pg, out, extra=0):
                    box = pg.locator('.gc').bounding_box()
                    pg.screenshot(path=str(docs / out), clip={'x': 0, 'y': 0, 'width': pg.viewport_size['width'],
                                                               'height': box['y'] + box['height'] + 16 + extra})

                def sent(pg):
                    return pg.evaluate('window.__sent')

                # Email: screenshot, inline subject edit -> prompt with draft id and the new subject.
                pg = open_page('email')
                snap(pg, 'email-pending.png')
                subj = pg.locator('[data-f="subject"]')
                subj.click()
                pg.keyboard.press('Control+A')
                pg.keyboard.type('Re: Q4 venue contract, notes inside')
                pg.locator('#sendb').click()
                p = sent(pg)
                assert len(p) == 1 and p[0].startswith('Send draft em-7f3a with my edits'), p
                assert 'SUBJECT: Re: Q4 venue contract, notes inside' in p[0] and 'BODY' not in p[0], p
                pg.wait_for_timeout(100)
                pill = pg.locator('details.done')
                assert pill.count() == 1 and pill.locator('.gt').text_content() == 'Re: Q4 venue contract, notes inside'
                passed.append('email inline subject edit -> edit prompt, card collapses with the edit')

                # Email: unchanged Send gives the exact-as-shown prompt.
                pg = open_page('email')
                pg.locator('#sendb').click()
                assert sent(pg) == ['Send draft em-7f3a exactly as shown.'], sent(pg)
                passed.append('unchanged send prompt')

                # Menu opens above the pill and its items send rule prompts naming the draft.
                pg = open_page('email')
                pg.locator('details.mm > summary').click()
                menu = pg.locator('.menu')
                assert menu.is_visible()
                assert menu.locator('.mi').count() == 4
                snap(pg, 'email-menu.png')
                menu.locator('.mi', has_text='Manage rules').click()
                assert sent(pg) == ['Draft rules: show me my draft rules. Keep draft em-7f3a waiting for me.'], sent(pg)
                passed.append('menu opens, rule prompt captured')

                # Emptying To disables Send; refilling enables it.
                pg = open_page('email')
                to = pg.locator('[data-f="to"]')
                to.click()
                pg.keyboard.press('Control+A')
                pg.keyboard.press('Delete')
                pg.wait_for_timeout(50)
                assert pg.locator('.sp').evaluate('e => e.classList.contains("off")')
                assert pg.locator('#sendb').is_disabled()
                assert pg.locator('details.mm > summary').is_visible()
                pg.locator('#sendb').click(force=True)
                assert sent(pg) == [], sent(pg)
                assert pg.locator('[data-f="to"]').evaluate('e => getComputedStyle(e, "::before").content') == '"name@example.com"'
                snap(pg, 'email-empty-to.png')
                to.click()
                pg.keyboard.type('dana.rivera@example.com')
                assert not pg.locator('#sendb').is_disabled()
                passed.append('Send disabled with empty To, placeholder shown, re-enabled')

                # Long body edit over the 500 character prompt cap: card says so and sends nothing.
                pg = open_page('email')
                pg.locator('[data-f="body"]').click()
                pg.keyboard.press('End')
                pg.keyboard.type(' P.S. one more thing.')
                pg.locator('#sendb').click()
                assert sent(pg) == [], sent(pg)
                assert 'too long' in pg.locator('#msg').inner_text()
                assert not pg.locator('#gc').evaluate('e => e.classList.contains("sent")')
                passed.append('over-length edit prompt blocked with a message')

                # Failed delivery ack unlocks the card.
                pg = open_page('discord')
                pg.evaluate('window.__ack={ok:false,error:"undelivered"}')
                pg.locator('#sendb').click()
                pg.wait_for_timeout(50)
                assert not pg.locator('#gc').evaluate('e => e.classList.contains("sent")')
                assert 'Not delivered' in pg.locator('#msg').inner_text()
                passed.append('failed ack unlocks the card')

                # Discord: body edit -> BODY block; screenshot first.
                pg = open_page('discord')
                snap(pg, 'discord.png')
                pg.locator('[data-f="body"]').click()
                pg.keyboard.press('Control+A')
                pg.keyboard.type("I'm in for Friday at 8pm ET.")
                pg.locator('#sendb').click()
                p = sent(pg)
                assert len(p) == 1 and 'dc-19b2' in p[0] and "BODY:\n<<<\nI'm in for Friday at 8pm ET.\n>>>" in p[0], p
                passed.append('discord inline body edit -> BODY prompt')

                # Discard via data-hermes-send.
                pg = open_page('linkedin')
                pg.locator('.dsb').click()
                assert sent(pg) == ['Discard draft li-55c0; do not send it.'], sent(pg)
                passed.append('discard prompt captured')

                # X post: screenshot, Post prompt, copy button works without throwing.
                pg = open_page('x')
                assert pg.locator('.xim').evaluate('e => e.naturalWidth') > 0
                snap(pg, 'x-post.png')
                pg.locator('.xp').click()
                assert sent(pg) == ['Send draft x-2c41 exactly as shown.'], sent(pg)
                passed.append('x post renders inline image, Post prompt captured')

                # Contrast: a host without --card must not give white-on-white Send/Post text.
                for name, sel in (('email-nocard', '.sd'), ('x-nocard', '.xp')):
                    pg = open_page(name)
                    color = pg.locator(sel).evaluate('e => getComputedStyle(e).color')
                    bg = pg.locator('.sp' if sel == '.sd' else sel).evaluate('e => getComputedStyle(e).backgroundColor')
                    assert color == 'rgb(11, 11, 12)' and bg == 'rgb(242, 242, 242)', (name, color, bg)
                pg = open_page('email-plain')
                color = pg.locator('.sd').evaluate('e => getComputedStyle(e).color')
                assert color == 'rgb(255, 255, 255)', color
                passed.append('Send/Post text readable with --card undefined and with no theme at all')

                # Sent pill: collapsed, then expanded read-only; discarded.
                pg = open_page('email-sent')
                assert pg.locator('button').count() == 0 and pg.locator('[contenteditable]').count() == 0
                assert 'Sent' in pg.locator('summary.pill').inner_text()
                snap(pg, 'sent.png')
                pg.locator('summary.pill').click()
                pg.wait_for_timeout(100)
                assert pg.locator('.body').is_visible()
                snap(pg, 'sent-expanded.png')
                pg = open_page('linkedin')
                snap(pg, 'linkedin.png')
                pg = open_page('email-discarded')
                assert 'Discarded' in pg.locator('summary.pill').inner_text()
                snap(pg, 'discarded.png')
                passed.append('sent/discarded pills collapse and expand, no buttons')

                # 390px phone width: no horizontal overflow in any state.
                for n in ('email', 'discord', 'linkedin', 'x'):
                    for suffix in ('', '-sent', '-discarded'):
                        pg = open_page(n + suffix, 390, 844)
                        if suffix:
                            pg.locator('summary.pill').click()
                        else:
                            pg.locator('details.mm > summary').click() if n != 'x' else None
                        assert pg.evaluate('document.documentElement.scrollWidth <= innerWidth'), n + suffix
                        if n == 'email' and not suffix:
                            box = pg.locator('.menu').bounding_box()
                            assert box['x'] >= 0 and box['x'] + box['width'] <= 390, box
                pg = open_page('linkedin', 390, 844)
                snap(pg, 'phone.png')
                passed.append('390px: no horizontal overflow, menu stays on screen')

                for width in (360, 600):
                    pg = open_page('github', width=width)
                    assert pg.locator('.gr .k').all_text_contents() == ['Repo', 'Branch', 'Changes']
                    assert pg.locator('.gr .k').last.evaluate('e=>getComputedStyle(e).whiteSpace') == 'nowrap'
                    assert pg.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert pg.locator('.md h3').first.inner_text() == 'Summary'
                    if width == 600:
                        snap(pg, 'github-pr.png')
                    pg.evaluate('window.__ack={ok:false}')
                    pg.locator('.prb').click()
                    assert pg.locator('.msg').inner_text() == 'Approval was not delivered. Try again.'
                    pg.evaluate('window.__ack={ok:true}')
                    pg.locator('.prb').click()
                    assert sent(pg) == ['Send draft github-demo exactly as shown.'] * 2
                    pg.close()
                pg = open_page('github-sent')
                pill = pg.locator('summary.pill')
                toggle = pill.locator('.tg')
                for expanded in (False, True):
                    pg.mouse.move(630, 890)
                    pg.wait_for_timeout(180)
                    assert toggle.evaluate('e=>getComputedStyle(e).opacity') == '0'
                    pill.hover(position={'x': 40, 'y': 20})
                    pg.wait_for_timeout(180)
                    assert toggle.evaluate('e=>getComputedStyle(e).opacity') == '1'
                    pg.wait_for_timeout(180)
                    assert toggle.evaluate('e=>getComputedStyle(e).backgroundColor') == 'rgba(0, 0, 0, 0)'
                    toggle.hover()
                    pg.wait_for_timeout(180)
                    assert toggle.evaluate('e=>getComputedStyle(e).backgroundColor') != 'rgba(0, 0, 0, 0)'
                    assert toggle.locator('.t-close' if expanded else '.t-open').is_visible()
                    if not expanded:
                        toggle.click()
                        assert pg.get_by_text('View on GitHub').get_attribute('href') == 'https://github.com/example/project/pull/42'
                passed.append('GitHub PR cards: metadata, approval retry, verified link and separate hover states')
                pg.close()

                assert not errors, errors
                assert not blocked, blocked
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
    for item in passed:
        print('PASS', item)
    print('LOCAL Edge headless, all non-local requests blocked (none attempted), zero page errors.')
    print('Screenshots: docs/email-pending.png, email-menu.png, email-empty-to.png, discord.png, x-post.png, '
          'sent.png, sent-expanded.png, discarded.png, phone.png (LinkedIn at 390px)')


if __name__ == '__main__':
    main()

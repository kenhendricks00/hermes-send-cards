"""Browser check: Send and Discard collapse the card into its pill right away (local Edge, offline)."""
import sys,importlib.util,json
from pathlib import Path
from playwright.sync_api import sync_playwright
import tempfile
ROOT=Path(__file__).resolve().parents[1];S=Path(tempfile.mkdtemp());EX=ROOT/'skills/send-cards/examples';spec=importlib.util.spec_from_file_location('dc',ROOT/'skills/send-cards/scripts/draft_card.py');dc=importlib.util.module_from_spec(spec);spec.loader.exec_module(dc)
W='<!doctype html><meta charset="utf-8"><style>:root{color-scheme:dark;--foreground:#f2f2f2;--muted-foreground:#9a9a9f;--border:#2c2c2e;--card:#1c1c1e}body{margin:0;padding:14px;background:#141414;font-family:Segoe UI,sans-serif;width:560px}</style><script>window.SENT=[];window.hermes={send:p=>{SENT.push(p);return Promise.resolve({ok:true})},maxLength:8000}</script>'
def load(g,name):
  t=S/('cc_'+name+'.html');t.write_text(W+dc.render(json.loads((EX/(name+'.json')).read_text(encoding='utf-8'))),encoding='utf-8');g.goto(t.as_uri());g.wait_for_timeout(200)
def state(g):g.wait_for_timeout(400);return g.evaluate("()=>{const d=document.querySelector('details.done');return d?[d.dataset.state||(/Discarded/.test(d.textContent)?'discarded':'sent'),d.querySelector('.st2').textContent]:null}")
with sync_playwright() as p:
 b=p.chromium.launch(channel='msedge',headless=True);g=b.new_page(viewport={'width':600,'height':900});g.route('http*://**',lambda r:r.abort())
 errs=[];g.on('pageerror',lambda e:errs.append(str(e)))
 load(g,'email');g.locator('.gb').click();g.keyboard.press('End');g.keyboard.type(' EDITED');g.click('#sendb');r=state(g);print('email send',r);assert r and r[0]=='sent'
 g.locator('summary.pill').click(position={'x':40,'y':20});assert 'EDITED' in g.locator('.done .gb').inner_text();
 load(g,'email');g.click('.dsb');r=state(g);print('email discard',r);assert r[0]=='discarded'
 load(g,'email');g.click('.mm summary');g.locator('.mi').nth(1).click();r=state(g);print('keep waiting',r);assert r is None and not g.evaluate("document.getElementById('gc').classList.contains('sent')")
 for n in ('x','discord','github'):
  if not (EX/(n+'.json')).exists():continue
  load(g,n);g.click('.xp,#sendb,#pr-send');r=state(g);print(n,'send',r);assert r and r[0]=='sent'
  load(g,n);g.click('.dsb,.xd');r=state(g);print(n,'discard',r);assert r[0]=='discarded'
 print('errors',errs);assert not errs;print('PASS');b.close()

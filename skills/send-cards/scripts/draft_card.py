#!/usr/bin/env python3
"""Render an outgoing-message draft card as a self-contained HTML widget.

Usage:
  python draft_card.py draft.json [-o out.html] [--state pending|sent|discarded] [--sent-at "2:14 PM"]

draft.json keys:
  draft_id (required)   stable id used in every button prompt
  revision?             int, shown as "Draft v2" when above 1
  channel?              gmail | outlook | email | discord | slack | linkedin | sms | imessage | x (other -> generic)
  kind?                 email | chat | dm, overrides the channel's layout (e.g. a GitHub PR as "email")
  channel_name?         display name override
  icon?                 image URL or local path shown in place of the outlined channel icon
  bot?                  agent name for the "Disable drafts for <bot>" menu item (default "Hermes")
  rule?                 "approval" (default) or "allow", from draft_rules.py check
  from?                 "you@example.com" or "@handle"
  to, cc?, bcc?         [ "addr" | {name?, address?} ]
  subject?              email layout only
  where?                "#general · Example Server" for chat channels
  reply_to?             {author?, date?, text}, quoted context
  body                  plain text; HTML escaped, http(s) URLs linkified
  attachments?          [ {name, size?, url?, path?} ]; X posts show url/path images inline
  avatar?               X only: image URL or local path, or "unavatar" to load unavatar.io/x/<handle>
  sent_at?              time text for the sent pill (--sent-at overrides)

Prints the absolute path of the written HTML file. Show it with ::preview{file="<path>"}.
The script makes no network calls. Local image paths are read and embedded as data: URIs.
"""
import argparse
import base64
import html
import json
import mimetypes
import os
import re
import sys

E = html.escape
MAX_PROMPT = 500          # Hermes rejects widget prompts longer than this
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# channel -> (display name, layout kind)
CHANNELS = {
    "gmail": ("Gmail", "email"),
    "outlook": ("Outlook", "email"),
    "email": ("Mail", "email"),
    "discord": ("Discord", "chat"),
    "slack": ("Slack", "chat"),
    "linkedin": ("LinkedIn", "dm"),
    "sms": ("Messages", "dm"),
    "imessage": ("iMessage", "dm"),
    "x": ("X", "dm"),
}
KINDS = ("email", "chat", "dm")

OUTLINE = {
    "email": ('<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" '
              'stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m4 7 8 6 8-6"/></svg>'),
    "chat": ('<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" '
             'stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/></svg>'),
}
OUTLINE["dm"] = OUTLINE["chat"]
PR_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M6 8.5v7M18 15.5V9a2 2 0 0 0-2-2h-4"/><path d="m14 4.5-2.5 2.5L14 9.5"/></svg>'
TOGGLE = ('<span class="tg" aria-hidden="true">'
          '<svg class="t-open" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m7 9 5-5 5 5M7 15l5 5 5-5"/></svg>'
          '<svg class="t-close" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m7 4 5 5 5-5M7 20l5-5 5 5"/></svg></span>')



CLIP = ('<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 '
        '2-2V8z"/><path d="M14 3v5h5"/></svg>')
CHECK = ('<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">'
         '<path d="m5 12 5 5 9-10"/></svg>')
COPY = ('<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6">'
        '<rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6.5A2.5 2.5 0 0 0 13.5 4h-7A2.5 2.5 0 0 0 '
        '4 6.5v7A2.5 2.5 0 0 0 6.5 16H8"/></svg>')

# Theme: Hermes injects --foreground, --muted-foreground, --border and --card. Each has a fallback so the
# card is readable in a plain browser or a host that defines only some of them.
CSS = """
.gc{--fg:var(--foreground,#16171a);--mfg:var(--muted-foreground,#6b6f78);--bd:var(--border,#d9dbe0);--cd:var(--card,#ffffff);
 --g-bg:color-mix(in srgb,var(--cd) 92%,var(--fg) 8%);max-width:600px;border-radius:24px;color:var(--fg);
 background:var(--g-bg);border:1px solid color-mix(in srgb,var(--bd) 70%,transparent);padding:20px 22px 18px;
 box-sizing:border-box;line-height:1.4;font-size:15px;overflow-wrap:anywhere;font-family:inherit}
.gc *{box-sizing:border-box}
.hd{display:flex;align-items:center;gap:10px;margin-bottom:18px;min-width:0}
.oi{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;flex:none;color:var(--mfg);overflow:hidden;
 background:color-mix(in srgb,var(--fg) 6%,transparent);box-shadow:0 0 0 1px color-mix(in srgb,var(--fg) 12%,transparent)}
.oi img{width:100%;height:100%;object-fit:cover}
.mn{font-weight:500;font-size:16.5px;flex:none}
.dm{color:var(--mfg);font-size:14.5px;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tag{flex:none;font-size:12px;color:var(--mfg);padding:3px 9px;border-radius:999px;margin-left:auto;
 background:color-mix(in srgb,var(--fg) 8%,transparent)}
.gt{font-weight:600;font-size:17px;margin-bottom:10px;outline:none;min-height:1.3em}
.grs{margin-bottom:14px}
.gr{display:grid;grid-template-columns:52px minmax(0,1fr);gap:10px;align-items:baseline;padding:11px 0;
 border-bottom:1px solid color-mix(in srgb,var(--fg) 11%,transparent)}
.gr .k{color:var(--mfg);font-size:15px}
.gr .v{font-size:15.5px;min-width:0;overflow-wrap:anywhere;outline:none;min-height:1.2em}
.ed:empty:before{content:attr(data-ph);color:var(--mfg);opacity:.75}
.ed:focus{border-radius:6px;box-shadow:0 0 0 3px color-mix(in srgb,var(--fg) 9%,transparent);
 background:color-mix(in srgb,var(--fg) 9%,transparent)}
.gb{white-space:pre-wrap;font-size:15.5px;line-height:1.55;outline:none;padding:2px 0;word-break:break-word;min-height:1.5em}
.gb a{color:inherit;text-decoration:underline;text-decoration-color:color-mix(in srgb,var(--fg) 40%,transparent);text-underline-offset:2px}
details.q{margin:0 0 12px}
details.q>summary{list-style:none;cursor:pointer;border-left:3px solid color-mix(in srgb,var(--mfg) 55%,transparent);
 padding:2px 0 2px 12px;color:var(--mfg);font-size:14px}
details.q>summary::-webkit-details-marker{display:none}
.q .qa{font-size:12.5px;margin-bottom:2px;display:flex;gap:6px}
.q .qa b{font-weight:600;color:var(--fg);opacity:.75}
.q .qt{white-space:pre-wrap;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
details.q[open] .qt{display:block;-webkit-line-clamp:unset}
.at{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.af{display:inline-flex;align-items:center;gap:8px;max-width:100%;padding:7px 12px 7px 9px;border-radius:12px;font-size:14px;
 background:color-mix(in srgb,var(--fg) 7%,transparent);border:1px solid color-mix(in srgb,var(--bd) 80%,transparent)}
.af svg{flex:none;opacity:.7}
.af span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.af small{color:var(--mfg);font-size:12.5px;flex:none}
.chev{width:8px;height:8px;border-right:1.8px solid var(--cd);border-bottom:1.8px solid var(--cd);transform:rotate(45deg) translate(-2px,-2px)}
.gf{display:flex;justify-content:flex-end;align-items:center;gap:14px;margin-top:20px}
.dsb{all:unset;cursor:pointer;color:var(--mfg);font-size:15.5px;padding:8px 14px;border-radius:999px;transition:background-color .15s,color .15s}.dsb:hover{background:color-mix(in srgb,currentColor 14%,transparent);color:var(--fg,var(--foreground))}
.sp{display:flex;align-items:center;background:var(--fg);color:var(--cd);border-radius:999px;position:relative}
.sd{all:unset;cursor:pointer;height:40px;display:flex;align-items:center;padding:0 14px 0 22px;font-size:15.5px;font-weight:500}
.sep{width:1px;height:18px;background:color-mix(in srgb,var(--cd) 30%,transparent)}
details.mm>summary{list-style:none;cursor:pointer;height:40px;padding:0 16px 0 12px;display:flex;align-items:center}
details.mm>summary::-webkit-details-marker{display:none}
.menu{position:absolute;right:0;bottom:calc(100% + 8px);width:min(270px,calc(100vw - 24px));padding:6px;border-radius:16px;z-index:5;
 background:color-mix(in srgb,var(--cd) 88%,var(--fg) 12%);border:1px solid var(--bd);
 box-shadow:0 10px 30px rgba(0,0,0,.35);display:flex;flex-direction:column}
.mi{all:unset;cursor:pointer;padding:10px 12px;border-radius:10px;font-size:14.5px;color:var(--fg);display:flex;align-items:center;justify-content:space-between;gap:12px}
.mi>span{display:flex;flex-direction:column;gap:2px;min-width:0}.mi b{font-weight:500}.mi small{color:var(--mfg);font-size:12.5px}
.mi svg{flex:none;color:var(--mfg)}
.mi:hover{background:color-mix(in srgb,var(--fg) 9%,transparent)}
.sp.off{background:transparent;align-items:stretch}
.sp.off .sd{cursor:default;color:var(--mfg);background:color-mix(in srgb,var(--fg) 14%,transparent);border-radius:999px 0 0 999px}
.sp.off .sep{display:none}
.sp.off details.mm>summary{background:var(--fg);border-radius:0 999px 999px 0}
.gt.ed:empty:before{font-weight:600}
.gc.sent .gf,.gc.sent .xf{opacity:.5;pointer-events:none}
.msg{margin-top:10px;font-size:13.5px;color:#d93f3f}
.msg:empty{display:none}
@media (max-width:380px){.gc{padding:16px}.gr{grid-template-columns:44px minmax(0,1fr)}}
"""

DONE_CSS = """
.gc.done{padding:0}
.gc.done>summary.pill{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;padding:16px 20px;min-width:0}
.gc.done>summary.pill::-webkit-details-marker{display:none}
.gc.done .mn{font-size:16px}
.gc.done .st2{flex:1}
.gc.done .body{padding:4px 22px 20px}
.gc.done .xims{margin-top:12px}

.tg{width:28px;height:28px;border-radius:999px;display:grid;place-items:center;flex:none;margin:-4px -6px -4px 0;
 color:var(--mfg);transition:opacity .15s ease,background-color .15s ease,color .15s ease}
.tg svg{grid-area:1/1}.gc.done[open] .t-open,.gc.done:not([open]) .t-close{display:none}
.gc.done>summary.pill .tg:hover{background:color-mix(in srgb,var(--fg) 10%,transparent);color:var(--fg)}
@media(hover:hover) and (pointer:fine){.tg{opacity:0}.gc.done:hover .tg,.gc.done>summary.pill:focus-visible .tg{opacity:1}}
.gc.pr .gr{grid-template-columns:68px minmax(0,1fr)}.gc.pr .gr .k{white-space:nowrap}
.prh{color:var(--mfg);font-size:15px;margin-left:2px}
.gr .v code,.md code{font:12.5px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;padding:1px 6px;border-radius:6px;
 background:color-mix(in srgb,var(--fg) 9%,transparent)}
.br{display:inline-flex;flex-wrap:wrap;align-items:center;gap:6px}.br .ar{color:var(--mfg)}
.md{font-size:14.5px;line-height:1.55;word-break:break-word}
.md h2,.md h3,.md h4{font-size:15.5px;font-weight:600;margin:14px 0 6px}.md>:first-child{margin-top:0}
.md p{margin:0 0 9px}.md ul{margin:0 0 9px;padding-left:20px}.md li{margin:3px 0}
.md a{color:inherit;text-decoration:underline;text-underline-offset:2px;text-decoration-color:color-mix(in srgb,var(--fg) 40%,transparent)}
.mdw.long .md{max-height:300px;overflow:hidden;-webkit-mask-image:linear-gradient(#000 75%,transparent);mask-image:linear-gradient(#000 75%,transparent)}
.mdw.long:has(details.sm[open]) .md{max-height:none;-webkit-mask-image:none;mask-image:none}
.prb{all:unset;cursor:pointer;background:var(--fg);color:var(--cd);border-radius:999px;padding:10px 22px;font-size:15.5px;font-weight:500}
.gc.pr .sm>summary{cursor:pointer;color:var(--mfg);padding:4px 0}.gc.pr .sm[open] .n1,.gc.pr .sm:not([open]) .n2{display:none}
.gc.pr :is(.prb,.dsb):focus-visible{outline:2px solid var(--fg);outline-offset:2px}
.gc.pr .mn{min-width:0}.gc.pr .hd{flex-wrap:wrap}.gc.pr .ar{flex:none}.gc.pr .br{min-width:0}.gc.pr .br code{overflow-wrap:anywhere}.gc.pr .st2{overflow-wrap:anywhere}

"""

XCSS = """
.xk{padding:22px 22px 20px;border-radius:26px}
.xh{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.xav{width:46px;height:46px;border-radius:50%;object-fit:cover;flex:none;display:grid;place-items:center;
 font-weight:600;font-size:19px;color:var(--mfg);background:color-mix(in srgb,var(--fg) 12%,transparent)}
.xn{display:flex;flex-direction:column;min-width:0;flex:1}.xn b{font-weight:500;font-size:17px}
.xn span{color:var(--mfg);font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.xc{all:unset;cursor:pointer;color:var(--mfg);padding:6px;border-radius:10px;display:grid;place-items:center}
.xc:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}.xc.ok{color:#22a35a}
.xb{white-space:pre-wrap;font-size:16px;line-height:1.55;word-break:break-word}
.xb a{color:#1d9bf0;text-decoration:none}
.xims{display:grid;gap:6px;margin-top:12px}.xim{width:100%;border-radius:16px;display:block;border:1px solid var(--bd)}
.xf{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:20px}
.xbtn{border:none;border-radius:999px;padding:13px 0;font:inherit;font-size:16px;cursor:pointer}
.xd{background:color-mix(in srgb,var(--fg) 12%,transparent);color:var(--fg)}
.xp{background:var(--fg);color:var(--cd);font-weight:500}
.xbtn:active,.sd:active{transform:scale(.98)}
"""

# The Send/Post pill uses the foreground color as background and the card color as text. A host that
# leaves --card undefined or equal to the foreground gives white on white, so pick the text color from
# the pill's real computed background. Reads only this widget's own computed styles.
CONTRAST_JS = """<script>(function(){function fix(){document.querySelectorAll('.xp,.sp,.prb').forEach(b=>{
 const m=getComputedStyle(b).backgroundColor.match(/[\\d.]+/g);if(!m||m.length<3||(m[3]!==undefined&&+m[3]<0.5))return;
 const L=(0.299*m[0]+0.587*m[1]+0.114*m[2])/255;const c=L>0.6?'#0b0b0c':'#ffffff';
 b.style.color=c;b.querySelectorAll('.chev').forEach(v=>v.style.borderColor=c)})}
addEventListener('DOMContentLoaded',fix);addEventListener('load',fix);setTimeout(fix,300)})()</script>"""

URL_RE = re.compile(r'(https?://[^\s<>"\']+[^\s<>"\'.,;:!?)\]])')
SAFE_SRC_RE = re.compile(r'^(https?://|data:image/(png|jpeg|gif|webp);base64,)', re.I)


class DraftError(ValueError):
    pass


def js_json(value):
    """JSON for inline <script>: escape <, > and & so data can never close the script tag."""
    return (json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def linkify(text):
    """Escape text and turn bare http(s) URLs into links. Nothing else becomes a link."""
    out, pos = [], 0
    for m in URL_RE.finditer(text):
        out.append(E(text[pos:m.start()]))
        u = m.group(1)
        out.append(f'<a href="{E(u)}" target="_blank" rel="noopener noreferrer">{E(u)}</a>')
        pos = m.end()
    out.append(E(text[pos:]))
    return "".join(out)


def safe_src(u):
    """Only http(s) and base64 raster data URIs may be used as image sources."""
    u = (u or "").strip()
    return u if SAFE_SRC_RE.match(u) else ""


def embed_image(path, base_dir):
    """Read a local image the draft points at and return a data: URI, or "" if unusable."""
    p = path if os.path.isabs(path) else os.path.join(base_dir or ".", path)
    mime = mimetypes.guess_type(p)[0] or ""
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return ""
    try:
        if os.path.getsize(p) > MAX_IMAGE_BYTES:
            return ""
        with open(p, "rb") as f:
            return f"data:{mime};base64,{base64.b64encode(f.read()).decode('ascii')}"
    except OSError:
        return ""


def image_src(value, base_dir):
    value = (value or "").strip()
    if not value:
        return ""
    if re.match(r'^[a-z][a-z0-9+.-]*:', value, re.I) and not re.match(r'^[a-z]:[\\/]', value, re.I):
        return safe_src(value)
    return embed_image(value, base_dir)


def x_length(text):
    """Approximate X length: every URL counts as 23 characters."""
    return len(URL_RE.sub("x" * 23, text))


MD_INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s<>()]+[^\s<>().,;:!?])")


def md_inline(t):
    out, pos = [], 0
    for m in MD_INLINE.finditer(t):
        out.append(E(t[pos:m.start()]))
        code, bold, ltext, lurl, bare = m.groups()
        if code is not None:
            out.append(f"<code>{E(code)}</code>")
        elif bold is not None:
            out.append(f"<b>{E(bold)}</b>")
        elif ltext is not None:
            out.append(f'<a href="{E(lurl)}" target="_blank" rel="noopener">{E(ltext)}</a>')
        else:
            out.append(f'<a href="{E(bare)}" target="_blank" rel="noopener">{E(bare)}</a>')
        pos = m.end()
    out.append(E(t[pos:]))
    return "".join(out)


def md(text):
    """Small, escaped Markdown subset for PR bodies: #/##/### headings, - / * bullets, paragraphs, inline code/bold/links."""
    html_out, para, items = [], [], []
    def flush():
        if para:
            html_out.append("<p>" + md_inline(" ".join(para)) + "</p>"); para.clear()
        if items:
            html_out.append("<ul>" + "".join(f"<li>{md_inline(i)}</li>" for i in items) + "</ul>"); items.clear()
    for raw in text.split("\n"):
        line = raw.rstrip()
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        b = re.match(r"^\s*[-*]\s+(.*)$", line)
        if not line.strip():
            flush()
        elif h:
            flush(); lvl = min(4, len(h.group(1)) + 1); html_out.append(f"<h{lvl}>{md_inline(h.group(2))}</h{lvl}>")
        elif b:
            if para:
                flush()
            items.append(b.group(1))
        elif items and raw.startswith("  "):
            items[-1] += " " + line.strip()
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return "".join(html_out)


def norm_people(lst):
    res = []
    for p in lst or []:
        if isinstance(p, str):
            res.append({"name": "", "address": p})
        elif isinstance(p, dict):
            res.append({"name": str(p.get("name", "") or ""), "address": str(p.get("address", "") or "")})
    return res


def clip(s, n):
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def channel_info(o):
    ch = str(o.get("channel") or "email").lower()
    name, kind = CHANNELS.get(ch, (ch.title(), "dm"))
    kind = str(o.get("kind") or kind).lower()
    if kind not in KINDS:
        raise DraftError(f"kind must be one of {', '.join(KINDS)}")
    return ch, str(o.get("channel_name") or name), kind


def attachments_html(att):
    if not att:
        return ""
    return '<div class="at">' + "".join(
        f'<span class="af" title="{E(str(a.get("name", "")))}">{CLIP}<span>{E(str(a.get("name", "file")))}</span>'
        f'{"<small>" + E(str(a["size"])) + "</small>" if a.get("size") else ""}</span>' for a in att) + "</div>"


def quote_html(rt):
    if not isinstance(rt, dict) or not rt.get("text"):
        return ""
    meta = ""
    if rt.get("author") or rt.get("date"):
        meta = (f'<div class="qa"><b>{E(str(rt.get("author", "")))}</b>'
                f'{"<span>" + E(str(rt["date"])) + "</span>" if rt.get("date") else ""}</div>')
    return f'<details class="q"><summary>{meta}<div class="qt">{E(str(rt["text"]))}</div></summary></details>'


def _render(o, state="pending", base_dir=None):
    if not isinstance(o, dict):
        raise DraftError("draft JSON must be an object")
    if not str(o.get("draft_id", "")).strip():
        raise DraftError("draft_id is required")
    if state not in ("pending", "sent", "discarded"):
        raise DraftError("state must be pending, sent or discarded")
    ch, cname, kind = channel_info(o)
    did = str(o["draft_id"]).strip()
    try:
        rev = int(o.get("revision", 1) or 1)
    except (TypeError, ValueError):
        raise DraftError("revision must be a number")
    if ch == "github":
        return render_pr(o, state, "GitHub", did, rev, str(o.get("body") or ""))
    sender = str(o.get("from", "") or "")
    to, cc, bcc = norm_people(o.get("to")), norm_people(o.get("cc")), norm_people(o.get("bcc"))
    where = str(o.get("where", "") or "")
    text = str(o.get("body") or "").replace("\r\n", "\n").strip("\n")
    att = [a for a in (o.get("attachments") or []) if isinstance(a, dict)]
    quote = quote_html(o.get("reply_to"))
    att_html = attachments_html(att)
    icon_src = image_src(o.get("icon"), base_dir)
    oicon = (f'<span class="oi"><img src="{E(icon_src)}" alt=""></span>' if icon_src
             else f'<span class="oi">{OUTLINE[kind]}</span>')
    handle = (sender if sender.startswith("@") else ("@" + sender if sender else "")) if ch == "x" else ""
    x_imgs = ""
    if ch == "x":
        srcs = [image_src(a.get("url") or a.get("path"), base_dir) for a in att]
        x_imgs = "".join(f'<img class="xim" src="{E(s)}" alt="">' for s in srcs if s)

    def who(p):
        return (p["address"] or p["name"]) if kind == "email" else (p["name"] or p["address"])

    def ppl(lst):
        return ", ".join(who(p) for p in lst)

    if state in ("sent", "discarded"):
        if to:
            rcpt = who(to[0]) + (f" +{len(to) - 1}" if len(to) > 1 else "")
        else:
            rcpt = where or handle
        tail = f" \u00b7 {E(rcpt)}" if rcpt else ""
        ro = []
        if sender:
            ro.append(("From", sender))
        if kind == "email":
            ro += [(k, ppl(lst)) for k, lst in (("To", to), ("Cc", cc), ("Bcc", bcc)) if lst]
        else:
            if where:
                ro.append(("In", where))
            if to:
                ro.append(("To", ppl(to)))
        ttl = str(o.get("subject") or "") if kind == "email" else ""
        rbody = ((f'<div class="gt">{E(ttl)}</div>' if ttl else "")
                 + '<div class="grs">' + "".join(f'<div class="gr"><span class="k">{k}</span><span class="v">{E(v)}</span></div>'
                                                 for k, v in ro) + "</div>"
                 + quote + f'<div class="gb">{linkify(text)}</div>'
                 + (f'<div class="xims">{x_imgs}</div>' if x_imgs else att_html))
        label = "Discarded" if state == "discarded" else "Sent"
        when = f" \u00b7 {E(str(o['sent_at']))}" if state == "sent" and o.get("sent_at") else ""
        return (f'<style>{CSS}{DONE_CSS}{XCSS}</style>\n'
                f'<details class="gc done" data-state="{state}" data-draft="{E(did)}"><summary class="pill">{oicon}'
                f'<span class="mn">{E(cname)}</span><span class="dm st2">{label}{tail}{when}</span>{TOGGLE}</summary>\n'
                f' <div class="body">{rbody}</div>\n</details>')

    discard_prompt = f"Discard draft {did}; do not send it."

    if ch == "x":
        av = str(o.get("avatar") or "").strip()
        if av.lower() == "unavatar":
            av_src = f"https://unavatar.io/x/{handle[1:]}" if len(handle) > 1 else ""
        else:
            av_src = image_src(av, base_dir)
        initial = E((handle[1:2] or "?").upper())
        av_html = (f'<img class="xav" src="{E(av_src)}" alt="" onerror="this.style.visibility=\'hidden\'">' if av_src
                   else f'<span class="xav">{initial}</span>')
        return f"""<style>{CSS}{XCSS}</style>
<div class="gc xk" id="gc" data-state="pending" data-draft="{E(did)}">
 <div class="xh">{av_html}<div class="xn"><b>Post</b><span>{E(handle)}</span></div>
  <button class="xc" id="xcopy" title="Copy" aria-label="Copy post text">{COPY}</button></div>
 <div class="xb">{linkify(text)}</div>{('<div class="xims">' + x_imgs + '</div>') if x_imgs else ''}
 <div class="xf"><button class="xbtn xd" data-hermes-send="{E(discard_prompt)}">Discard</button>
  <button class="xbtn xp" data-hermes-send="{E(f'Send draft {did} exactly as shown.')}">Post</button></div>
</div>
<script>(function(){{const gc=document.getElementById('gc'),t={js_json(text)};
gc.querySelectorAll('[data-hermes-send]').forEach(b=>b.addEventListener('click',()=>{{gc.classList.add('sent');if(b.classList.contains('xp'))setTimeout(()=>window.__dcDone&&window.__dcDone('sent'),120)}}));
const c=document.getElementById('xcopy');c.addEventListener('click',()=>{{
 const done=()=>{{c.classList.add('ok');setTimeout(()=>c.classList.remove('ok'),1200)}};
 (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(done).catch(()=>{{
  const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();try{{document.execCommand('copy');done()}}catch(e){{}}a.remove()}})}});
}})()</script>
{CONTRAST_JS}"""

    # ---- pending message card: title, From/To/Cc rows, editable body, Discard + Send split pill
    if kind == "email":
        title = str(o.get("subject") or "")
    else:
        title = where or (who(to[0]) if to else cname)
    rows = []
    if sender:
        rows.append(f'<div class="gr"><span class="k">From</span><span class="v">{E(sender)}</span></div>')

    def edit_row(label, value, field):
        return (f'<div class="gr"><span class="k">{label}</span><span class="v ed" contenteditable="plaintext-only" '
                f'spellcheck="false" data-f="{field}" data-ph="name@example.com">{E(value)}</span></div>')

    if kind == "email":
        rows.append(edit_row("To", ppl(to), "to"))
        rows.append(edit_row("Cc", ppl(cc), "cc"))
        if bcc:
            rows.append(edit_row("Bcc", ppl(bcc), "bcc"))
    else:
        # The title already names the channel; the To row adds the detail (headline, number) after the name.
        if where and where != title:
            rows.append(f'<div class="gr"><span class="k">In</span><span class="v">{E(where)}</span></div>')
        if to:
            full = ", ".join(" \u00b7 ".join(x for x in (p["name"], p["address"]) if x) for p in to)
            rows.append(f'<div class="gr"><span class="k">To</span><span class="v">{E(full)}</span></div>')
    title_html = (f'<div class="gt ed" contenteditable="plaintext-only" data-f="subject" data-ph="Subject">{E(title)}</div>'
                  if kind == "email" else f'<div class="gt">{E(title)}</div>')
    body_html = f'<div class="gb ed" contenteditable="plaintext-only" data-f="body" data-ph="Message">{E(text)}</div>'

    rcpt = clip(ppl(to) or where or "this recipient", 120)
    bot = clip(str(o.get("bot") or "Hermes"), 60)
    rule = o.get("rule", "approval")
    items = [
        ("Allow recipient", "Adds this recipient to your Allow rules", rule == "allow",
         f"Draft rules: allow {kind} to {rcpt} without a draft card from now on (add the Allow rule), "
         f"then send draft {did} exactly as shown."),
        ("Needs approval", "Ask again next time", rule != "allow",
         f"Draft rules: {kind} to {rcpt} needs approval (remove any Allow rule). Keep draft {did} waiting for me."),
        (f"Disable drafts for {bot}", "Turns off draft cards for this agent", False,
         f"Draft rules: disable draft cards for {bot} (it may send without asking). Keep draft {did} waiting for me."),
        ("Manage rules", "", False, f"Draft rules: show me my draft rules. Keep draft {did} waiting for me."),
    ]
    menu_html = "".join(
        f'<button class="mi" data-hermes-send="{E(pr)}"><span><b>{E(lb)}</b>'
        f'{("<small>" + E(sub) + "</small>") if sub else ""}</span>{CHECK if on else ""}</button>'
        for lb, sub, on, pr in items)
    tag = f'<span class="tag">Draft v{rev}</span>' if rev > 1 else ""
    orig = js_json({"to": ppl(to), "cc": ppl(cc), "bcc": ppl(bcc), "subject": str(o.get("subject") or ""), "body": text})
    need = ["to", "body"] if kind == "email" else ["body"]
    return f"""<style>{CSS}</style>
<div class="gc" id="gc" data-state="pending" data-draft="{E(did)}">
 <div class="hd">{oicon}<span class="mn">{E(cname)}</span>{tag}</div>
 {title_html}
 <div class="grs">{''.join(rows)}</div>
 {quote}{body_html}{att_html}
 <div class="gf"><button class="dsb" data-hermes-send="{E(discard_prompt)}">Discard</button>
  <div class="sp"><button class="sd" id="sendb">Send</button><span class="sep"></span>
   <details class="mm"><summary aria-label="Send options"><span class="chev"></span></summary><div class="menu">{menu_html}</div></details></div></div>
 <div class="msg" id="msg" role="status"></div>
</div>
<script>(function(){{
const gc=document.getElementById('gc'),O={orig},id={js_json(did)},MAX={MAX_PROMPT},msg=document.getElementById('msg');
const val=f=>{{const e=gc.querySelector('[data-f="'+f+'"]');return e?e.innerText.replace(/\\u00a0/g,' ').trim():(O[f]||'')}};
const sp=gc.querySelector('.sp'),sb=document.getElementById('sendb'),need={js_json(need)};
const ok=()=>need.every(f=>val(f).length>0);
const upd=()=>{{gc.querySelectorAll('.ed').forEach(e=>{{if(!e.innerText.trim())e.textContent=''}});
 const on=ok();sp.classList.toggle('off',!on);sb.disabled=!on;sb.setAttribute('aria-disabled',String(!on))}};
gc.addEventListener('input',upd);upd();
const lock=()=>{{gc.classList.add('sent');gc.querySelectorAll('.ed').forEach(e=>e.contentEditable='false')}};
const unlock=t=>{{gc.classList.remove('sent');gc.querySelectorAll('.ed').forEach(e=>e.contentEditable='plaintext-only');msg.textContent=t}};
sb.addEventListener('click',()=>{{if(!ok())return;
 const cur={{to:val('to'),cc:val('cc'),bcc:val('bcc'),subject:val('subject'),body:val('body')}};
 const ch=Object.keys(cur).filter(k=>(cur[k]||'')!==(O[k]||'').trim());
 let p='Send draft '+id+' exactly as shown.';
 if(ch.length){{p='Send draft '+id+' with my edits, exactly as written below. Do not change anything else.\\n'+
  ch.filter(k=>k!=='body').map(k=>k.toUpperCase()+': '+cur[k]).join('\\n')+(ch.includes('body')?'\\nBODY:\\n<<<\\n'+cur.body+'\\n>>>':'')}}
 const max=(window.hermes&&window.hermes.maxLength)||MAX;
 if(p.length>max){{msg.textContent='These edits are too long to send from the card. Describe the change in chat and a new card will follow.';return}}
 msg.textContent='';lock();
 if(window.hermes&&window.hermes.send){{Promise.resolve(window.hermes.send(p)).then(a=>{{
  if(a&&a.ok===false)unlock(a.error==='throttled'?'Too fast. Tap Send again.':'Not delivered. Tap Send again.');
  else window.__dcDone&&window.__dcDone('sent',ch.length?cur:null)}},()=>unlock('Not delivered. Tap Send again.'))}}
}});
gc.querySelectorAll('[data-hermes-send]').forEach(b=>b.addEventListener('click',()=>{{
 const p=b.getAttribute('data-hermes-send')||'';if(/Keep draft .* waiting/.test(p)){{const m=b.closest('details');if(m)m.open=false;return}}
 lock();if(/then send draft/.test(p))setTimeout(()=>window.__dcDone&&window.__dcDone('sent'),120)}}));
}})()</script>
{CONTRAST_JS}"""


def render_pr(o, state, cname, did, rev, text):
    """GitHub pull request draft: repo, branch and change rows, Markdown body, Open/Update PR button."""
    repo = o.get("repo", "")
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise DraftError("GitHub drafts require an owner/repo")
    num = o.get("pr_number")
    if num is not None and (type(num) is not int or num < 1):
        raise DraftError("pr_number must be a positive integer")
    if o.get("action") == "update" and num is None:
        raise DraftError("PR updates require pr_number")
    update = num is not None and (state != "sent" or o.get("action") != "open")
    what = f"Pull request #{num}" if num else "New pull request"
    rows = []
    if repo:
        rows.append(("Repo", f"<code>{E(repo)}</code>"))
    if o.get("base") or o.get("head"):
        rows.append(("Branch", f'<span class="br"><code>{E(o.get("base", "main"))}</code><span class="ar">\u2190</span><code>{E(o.get("head", ""))}</code></span>'))
    if o.get("changes"):
        rows.append(("Changes", E(o["changes"])))
    rows_html = "".join(f'<div class="gr"><span class="k">{k}</span><span class="v">{v}</span></div>' for k, v in rows)
    long_ = sum(max(1, (len(line) + 69) // 70) for line in text.splitlines()) > 14
    body = (f'<div class="mdw{" long" if long_ else ""}"><div class="md">{md(text)}</div>'
            + ('<details class="sm"><summary><span class="n1">Show more</span><span class="n2">Show less</span>'
               '<span class="chev"></span></summary></details>' if long_ else "") + "</div>")
    oi = f'<span class="oi">{PR_ICON}</span>'
    title = f'<div class="gt">{E(o.get("subject", ""))}</div>'
    if state in ("sent", "discarded"):
        label = "Discarded" if state == "discarded" else ("Updated" if update else "Opened")
        ref = repo + (f" #{num}" if num else "")
        tail = " · " + E(ref) if ref else ""
        when = f" \u00b7 {E(o['sent_at'])}" if state == "sent" and o.get("sent_at") else ""
        link = (f'<div class="gf" style="justify-content:flex-start;margin-top:14px"><a class="dsb" href="{E(o["url"])}" target="_blank" rel="noopener">View on GitHub \u2197</a></div>'
                if state == "sent" and num is not None and o.get("url") == f"https://github.com/{repo}/pull/{num}" else "")
        return f"""<style>{CSS}{DONE_CSS}</style>
<details class="gc done gk pr"><summary class="pill">{oi}<span class="mn">{E(cname)}</span><span class="dm st2">{label}{tail}{when}</span>{TOGGLE}</summary>
 <div class="body">{title}<div class="grs">{rows_html}</div>{body}{link}</div>
</details>{CONTRAST_JS}"""
    discard = E(f"Discard draft {did}; do not send it.")
    approve = js_json(f"Send draft {did} exactly as shown.")
    btn = "Update PR" if update else "Open PR"
    return f"""<style>{CSS}{DONE_CSS}</style>
<div class="gc gk pr" id="gc">
 <div class="hd">{oi}<span class="mn">{E(cname)}</span><span class="prh">{E(what)}</span>{('<span class="tag">Draft v' + str(rev) + '</span>') if rev > 1 else ''}</div>
 {title}
 <div class="grs">{rows_html}</div>
 {body}
 <div class="gf"><button class="dsb" data-hermes-send="{discard}">Discard</button>
  <button class="prb" id="pr-send">{btn}</button></div>
 <div class="msg" role="status"></div>
</div>
<script>(function(){{const gc=document.getElementById('gc'), btn=document.getElementById('pr-send'), msg=gc.querySelector('.msg');
btn.addEventListener('click',async()=>{{
 if(btn.disabled)return;
 btn.disabled=true;msg.textContent='';gc.classList.add('sent');
 try{{
  if(!window.hermes?.send)throw Error();
  const ack=await window.hermes.send({approve});
  if(ack?.ok===false)throw Error();
  window.__dcDone&&window.__dcDone('sent');
 }}catch(e){{btn.disabled=false;gc.classList.remove('sent');msg.textContent='Approval was not delivered. Try again.'}}
}});
gc.querySelector('.dsb').addEventListener('click',()=>{{btn.disabled=true;gc.classList.add('sent')}});
}})()</script>
{CONTRAST_JS}"""


SWAP_JS = """<script>(function(){
// Collapse the card into its completed pill the moment the choice is made, like Grok Bot does.
// The agent still re-renders the card with the final state; this keeps the chat from looking frozen.
window.__dcDone=function(state,cur){
 const gc=document.getElementById('gc'),t=document.getElementById('dc-'+state);
 if(!gc||!t||gc.dataset.done)return;gc.dataset.done='1';
 const d=t.content.firstElementChild.cloneNode(true);
 if(cur){const set=(sel,v)=>{const e=d.querySelector(sel);if(e&&v)e.textContent=v};
  set('.gt',cur.subject);set('.gb',cur.body);
  d.querySelectorAll('.gr').forEach(r=>{const k=(r.querySelector('.k')||{}).textContent,v=r.querySelector('.v');
   const f={To:'to',Cc:'cc',Bcc:'bcc'}[k];if(f&&v&&cur[f])v.textContent=cur[f]})}
 gc.replaceWith(d)};
document.querySelectorAll('#gc .dsb,#gc .xd').forEach(b=>b.addEventListener('click',()=>setTimeout(()=>window.__dcDone('discarded'),120)));
})()</script>"""


def render(o, state="pending", base_dir=None):
    html = _render(o, state, base_dir)
    if state != "pending":
        return html

    def pill(st):
        # Nothing has been confirmed yet, so drop any sent time or PR link the draft might carry.
        out = _render({k: v for k, v in o.items() if k not in ("sent_at", "url", "number")}, st, base_dir)
        return out.split("</style>", 1)[1].strip() if "</style>" in out else out

    return (html + f"\n<style>{DONE_CSS}{XCSS}</style>"
            + f'<template id="dc-sent">{pill("sent")}</template>'
            + f'<template id="dc-discarded">{pill("discarded")}</template>\n{SWAP_JS}')


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render an outgoing-message draft card as HTML.")
    ap.add_argument("draft_json")
    ap.add_argument("-o", "--out")
    ap.add_argument("--state", default="pending", choices=["pending", "sent", "discarded"])
    ap.add_argument("--sent-at", help="time text for the sent pill, e.g. '2:14 PM'")
    a = ap.parse_args(argv)
    try:
        with open(a.draft_json, encoding="utf-8") as f:
            o = json.load(f)
    except OSError as e:
        ap.exit(2, f"draft_card: cannot read {a.draft_json}: {e.strerror or e}\n")
    except json.JSONDecodeError as e:
        ap.exit(2, f"draft_card: {a.draft_json} is not valid JSON: {e}\n")
    if a.sent_at and isinstance(o, dict):
        o["sent_at"] = a.sent_at
    try:
        page = render(o, a.state, os.path.dirname(os.path.abspath(a.draft_json)))
    except DraftError as e:
        ap.exit(2, f"draft_card: {e}\n")
    if isinstance(o, dict) and str(o.get("channel", "")).lower() == "x" and x_length(str(o.get("body") or "")) > 280:
        print(f"draft_card: warning: post is about {x_length(str(o.get('body') or ''))} characters, over X's 280",
              file=sys.stderr)
    out = a.out or os.path.splitext(a.draft_json)[0] + ".html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(os.path.abspath(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

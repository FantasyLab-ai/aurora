import os, time, json
from datetime import datetime
from pathlib import Path

# Reuse CDP helpers
from scripts.pipeline import _get_browser_ws, _list_targets, _CDPConn

CHROME_REMOTE = os.getenv("CHROME_REMOTE","127.0.0.1:9222")
DATA_DIR = Path(os.getenv("DATA_DIR", os.path.expanduser("~/FantasyAI/data")))
OUT = DATA_DIR / "public_metrics.jsonl"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _open_cdp_tab(url_substr: str, open_url: str | None = None, timeout: int = 30):
    """
    Attach to an existing tab that matches url_substr; if not found and open_url is given,
    create a new tab via CDP Target.createTarget and navigate there.
    """
    base = os.getenv("CHROME_REMOTE", CHROME_REMOTE)
    browser_ws = _get_browser_ws(base)
    bconn = _CDPConn(browser_ws)
    try:
        t0 = time.time()
        target = None
        while time.time() - t0 < timeout and target is None:
            for t in _list_targets(base):
                L = (t.get("url","") + " " + t.get("title","")).lower()
                if url_substr.lower() in L:
                    target = t
                    break
            if target is None:
                if open_url:
                    try:
                        res = bconn.call("Target.createTarget", {"url": open_url})
                        new_id = res.get("targetId")
                        t1 = time.time()
                        while time.time() - t1 < timeout and target is None:
                            for t in _list_targets(base):
                                if t.get("id") == new_id or t.get("url","") == open_url:
                                    target = t
                                    break
                            if target is None:
                                time.sleep(0.25)
                    except Exception:
                        pass
                if target is None:
                    time.sleep(0.4)
        if target is None:
            raise RuntimeError(f"could not find/open tab for {url_substr}")
        page_ws = target["webSocketDebuggerUrl"]
        tab = _CDPConn(page_ws)
        try:
            for dom in ("Page.enable","Runtime.enable","DOM.enable","Network.enable"):
                try: tab.call(dom, {})
                except Exception: pass
            if open_url:
                tab.call("Page.navigate", {"url": open_url})
                t2 = time.time()
                while time.time() - t2 < timeout:
                    rv = tab.call("Runtime.evaluate", {"expression":"document.readyState", "returnByValue":True})
                    if rv.get("result",{}).get("value") == "complete":
                        break
                    time.sleep(0.25)
            return tab
        except Exception:
            tab.close()
            raise
    finally:
        bconn.close()

def _eval(tab: _CDPConn, expr: str, rv=True, timeout=10):
    res = tab.call("Runtime.evaluate", {"expression": expr, "returnByValue": rv})
    return (res.get("result",{}) or {}).get("value")

def _scroll_page(tab: _CDPConn, steps=6, px=1200, delay=0.35):
    for _ in range(max(1, steps)):
        try:
            tab.call("Runtime.evaluate", {"expression": f"window.scrollBy(0,{int(px)});", "returnByValue": True})
        except Exception:
            pass
        time.sleep(delay)

def _write_jsonl(obj: dict):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

# ---------------- TikTok ----------------
def scrape_tiktok_profile(handle: str, max_videos=10):
    h = (handle or "").strip()
    if not h: raise ValueError("empty TikTok handle")
    if not h.startswith("@"): h = "@"+h
    url = f"https://www.tiktok.com/{h}"

    tab = _open_cdp_tab("tiktok.com", open_url=url, timeout=45)
    try:
        # soft scroll to load cards
        _scroll_page(tab, steps=8, px=1200, delay=0.3)

        js = f"""
        (function() {{
          function txt(sel) {{ const el = document.querySelector(sel); return el ? (el.textContent||'').trim() : ''; }}
          function toAbs(u){{ try{{return new URL(u, location.href).href;}}catch(e){{return u||'';}}}}
          const followers = txt('strong[data-e2e="followers-count"]');
          const following = txt('strong[data-e2e="following-count"]');
          const likesTotal = txt('strong[data-e2e="likes-count"]');
          // collect post cards (best-effort)
          const cards = Array.from(document.querySelectorAll('a[href*="/video/"]'));
          const uniq = new Map();
          for (const a of cards) {{
            const href = toAbs(a.getAttribute('href')||'');
            if (!href) continue;
            if (!uniq.has(href)) {{
              const viewsOverlay = (a.querySelector('strong')||{{}}).textContent || '';
              uniq.set(href, {{url: href, overlay: (viewsOverlay||'').trim()}});
            }}
            if (uniq.size >= {int(max_videos)}) break;
          }}
          return {{
            ok: true,
            meta: {{
              profile: {json.dumps(h)},
              followers, following, likesTotal,
              collected: uniq.size
            }},
            videos: Array.from(uniq.values())
          }};
        }})()
        """
        data = _eval(tab, js, rv=True) or {"ok": False}
        row = {
            "ts": datetime.utcnow().isoformat()+"Z",
            "platform": "tiktok",
            "profile": h,
            "data": data
        }
        _write_jsonl(row)
        return row
    finally:
        try: tab.close()
        except Exception: pass

# ---------------- YouTube ----------------
def scrape_youtube_channel(handle_or_url: str, max_videos=10):
    h = (handle_or_url or "").strip()
    if not h: raise ValueError("empty YouTube handle/url")
    if h.startswith("http"):
        url = h
    elif h.startswith("@"):
        url = f"https://www.youtube.com/{h}/videos"
    else:
        url = f"https://www.youtube.com/@{h}/videos"

    tab = _open_cdp_tab("youtube.com", open_url=url, timeout=45)
    try:
        _scroll_page(tab, steps=10, px=1400, delay=0.35)

        js = f"""
        (function() {{
          function toAbs(u){{ try{{return new URL(u, location.href).href;}}catch(e){{return u||'';}}}}
          function clean(s){{ return (s||'').replace(/\\s+/g,' ').trim(); }}
          // Try modern rich grid first
          let items = Array.from(document.querySelectorAll('ytd-rich-grid-media'));
          if (items.length === 0) {{
            items = Array.from(document.querySelectorAll('ytd-grid-video-renderer'));
          }}
          const out = [];
          for (const el of items) {{
            const a = el.querySelector('a#video-title');
            if (!a) continue;
            const title = clean(a.textContent||'');
            const href = toAbs(a.getAttribute('href')||'');
            const meta = el.querySelector('#metadata-line');
            let views = '', age = '';
            if (meta) {{
              const spans = meta.querySelectorAll('span.inline-metadata-item');
              if (spans && spans.length) {{
                views = clean(spans[0]?.textContent||'');
                age   = clean(spans[1]?.textContent||'');
              }} else {{
                const sp = meta.querySelectorAll('span');
                views = clean(sp[0]?.textContent||'');
                age   = clean(sp[1]?.textContent||'');
              }}
            }}
            out.push({{title, url: href, views, age}});
            if (out.length >= {int(max_videos)}) break;
          }}
          // Channel about stats (optional)
          const channelName = (document.querySelector('meta[itemprop="name"]')||{{}}).getAttribute?.('content')||'';
          return {{ ok: true, channelName, count: out.length, videos: out }};
        }})()
        """
        data = _eval(tab, js, rv=True) or {"ok": False}
        row = {
            "ts": datetime.utcnow().isoformat()+"Z",
            "platform": "youtube",
            "profile": h if h.startswith("@") else url,
            "data": data
        }
        _write_jsonl(row)
        return row
    finally:
        try: tab.close()
        except Exception: pass

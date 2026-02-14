from __future__ import annotations
import os, hmac, time, json, base64, hashlib, sqlite3, logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
import requests
import numpy as np
from scripts.embeddings import embed_reduced

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")

# Set these to go live (otherwise we run in DRY-RUN using local DB only)
TTSHOP_BASE_URL   = os.getenv("TTSHOP_BASE_URL",   "").strip()  # e.g. https://open-api.tiktokglobalshop.com
TTSHOP_APP_KEY    = os.getenv("TTSHOP_APP_KEY",    "").strip()
TTSHOP_APP_SECRET = os.getenv("TTSHOP_APP_SECRET", "").strip()
TTSHOP_ACCESS_TOK = os.getenv("TTSHOP_ACCESS_TOKEN","").strip()
TTSHOP_SHOP_ID    = os.getenv("TTSHOP_SHOP_ID",    "").strip()

def _db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS shop_products(
        product_id TEXT PRIMARY KEY,
        title TEXT,
        price REAL,
        sku_id TEXT,
        image_url TEXT,
        updated_ts INTEGER)""")
    con.execute("""CREATE TABLE IF NOT EXISTS topic_product_map(
        topic_key TEXT,
        product_id TEXT,
        reason TEXT,
        created_ts INTEGER,
        PRIMARY KEY(topic_key, product_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS video_product_links(
        platform TEXT, post_id TEXT, product_id TEXT,
        affiliate_url TEXT, created_ts INTEGER,
        PRIMARY KEY(platform, post_id, product_id))""")
    con.commit(); return con

def _sign(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    TikTok Shop Open Platform uses HMAC signatures. Because endpoints vary by region
    and spec changes, we only scaffold here. Provide BASE_URL/KEY/SECRET to enable.
    """
    if not (TTSHOP_BASE_URL and TTSHOP_APP_KEY and TTSHOP_APP_SECRET and TTSHOP_ACCESS_TOK):
        raise RuntimeError("TikTok Shop creds not set (DRY-RUN only).")

    # Example signature pattern (adjust per your region’s spec):
    # NOTE: Replace with the exact canonical ordering and algorithm from your docs.
    ts = int(time.time()*1000)
    params["app_key"] = TTSHOP_APP_KEY
    params["timestamp"] = ts
    qs = urlencode(sorted(params.items()))
    sign = hmac.new(TTSHOP_APP_SECRET.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest().upper()
    params["sign"] = sign
    return params

def _get(path:str, params:Dict[str,Any]) -> Dict[str,Any]:
    p = _sign(params)
    url = f"{TTSHOP_BASE_URL}{path}"
    r = requests.get(url, params=p, headers={"Authorization": f"Bearer {TTSHOP_ACCESS_TOK}"}, timeout=30)
    r.raise_for_status()
    return r.json()

def _post(path:str, body:Dict[str,Any]) -> Dict[str,Any]:
    p = _sign({})
    url = f"{TTSHOP_BASE_URL}{path}"
    r = requests.post(url, params=p, json=body, headers={"Authorization": f"Bearer {TTSHOP_ACCESS_TOK}"}, timeout=30)
    r.raise_for_status()
    return r.json()

def sync_products_from_api() -> int:
    """
    Pull products from TikTok Shop (if creds present).
    Falls back to DRY-RUN: keep whatever is in local DB.
    """
    con = _db()
    if not (TTSHOP_BASE_URL and TTSHOP_APP_KEY and TTSHOP_APP_SECRET and TTSHOP_ACCESS_TOK and TTSHOP_SHOP_ID):
        logging.info("TikTok Shop DRY-RUN: no creds, skipping remote sync.")
        cur = con.execute("SELECT COUNT(*) FROM shop_products")
        n = int(cur.fetchone()[0])
        con.close()
        return n

    # Example list call (adjust to your actual endpoint & payload)
    try:
        resp = _get("/api/products/list", {"shop_id": TTSHOP_SHOP_ID, "page_size": 100})
        items = resp.get("data", {}).get("products", [])
    except Exception as e:
        logging.warning("TikTok Shop list failed: %s", e)
        items = []

    n_up = 0; now = int(time.time())
    for it in items:
        pid = str(it.get("product_id"))
        title = (it.get("title") or "").strip()
        price = float(it.get("price", 0.0))
        sku_id = str((it.get("default_sku_id") or it.get("sku_id") or ""))
        img = (it.get("image") or "")
        con.execute("""INSERT OR REPLACE INTO shop_products(product_id,title,price,sku_id,image_url,updated_ts)
                       VALUES(?,?,?,?,?,?)""", (pid, title, price, sku_id, img, now))
        n_up += 1
    con.commit(); con.close()
    return n_up

def upsert_local_product(product_id:str, title:str, price:float, sku_id:str="", image_url:str=""):
    """Convenience for DRY-RUN or manual seeding."""
    con = _db(); now=int(time.time())
    con.execute("""INSERT OR REPLACE INTO shop_products(product_id,title,price,sku_id,image_url,updated_ts)
                   VALUES(?,?,?,?,?,?)""", (product_id, title, float(price), sku_id, image_url, now))
    con.commit(); con.close()

def list_products() -> list[dict]:
    con = _db()
    rows = con.execute("SELECT product_id,title,price,sku_id,image_url FROM shop_products ORDER BY updated_ts DESC").fetchall()
    con.close()
    out=[]
    for pid,title,price,sku,img in rows:
        out.append({"product_id":pid,"title":title,"price":price,"sku_id":sku,"image_url":img})
    return out

def suggest_products_for_topic(topic_key:str, topic_hint:str, top_k:int=3) -> list[dict]:
    """
    Embedding similarity between topic text and product titles.
    """
    products = list_products()
    if not products: return []
    e_topic = embed_reduced(topic_hint or topic_key or "")
    scored=[]
    for p in products:
        e = embed_reduced(p["title"] or "")
        sc = float(np.dot(e_topic, e))
        scored.append((sc,p))
    scored.sort(reverse=True)
    return [p for _,p in scored[:top_k]]

def link_video_to_products(platform:str, post_id:str, product_ids:list[str], affiliate_urls:Optional[list[str]]=None):
    con = _db(); now=int(time.time())
    affiliate_urls = affiliate_urls or [None]*len(product_ids)
    for pid, url in zip(product_ids, affiliate_urls):
        con.execute("""INSERT OR REPLACE INTO video_product_links(platform,post_id,product_id,affiliate_url,created_ts)
                       VALUES(?,?,?,?,?)""", (platform, post_id, pid, url, now))
    con.commit(); con.close()

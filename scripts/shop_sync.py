from __future__ import annotations
import time, argparse
from scripts.tiktok_shop_manager import sync_products_from_api, list_products, upsert_local_product

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="Seed a few demo products (DRY-RUN only).")
    args = ap.parse_args()

    if args.seed:
        upsert_local_product("owl-plush-001", "Handmade Owl Plush", 19.99)
        upsert_local_product("owl-book-101", "Guide to Saving Baby Owls", 12.95)
        upsert_local_product("wildlife-cam-4k", "Compact 4K Wildlife Camera", 129.00)

    n = sync_products_from_api()
    print(f"Synced products: {n}")
    for p in list_products():
        print("-", p["product_id"], p["title"], f"${p['price']:.2f}")

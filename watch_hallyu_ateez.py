import os, json, re, time
from pathlib import Path
import requests
from bs4 import BeautifulSoup

COLLECTION_URL = "https://hallyusuperstore.com/collections/ateez"
SEEN_FILE = Path("seen_products.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Instelbaar via env; bv INTERVAL_SEC=20
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "30"))
MAX_BACKOFF_SEC = 600  # 10 min

HEADERS = {"User-Agent": "Mozilla/5.0 (ATEEZ-Watcher/2.0)"}

def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            pass
    return set()

def save_seen(seen_set):
    SEEN_FILE.write_text(json.dumps(sorted(seen_set), ensure_ascii=False, indent=2))

def send_telegram(text):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("[INFO] Telegram niet geconfigureerd; melding alleen in console:\n", text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        print("[OK] Telegram-bericht verstuurd")
    except Exception as e:
        print(f"[WARN] Telegram fout: {e}\n{text}")

def parse_products_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    products = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/products/" not in href:
            continue
        if href.startswith("//"):
            url = "https:" + href
        elif href.startswith("/"):
            url = "https://hallyusuperstore.com" + href
        else:
            url = href
        m = re.search(r"/products/([^/?#]+)", url)
        if not m: 
            continue
        handle = m.group(1)
        title = a.get_text(strip=True) or a.get("title") or ""
        if not title:
            t = a.find(["span","div","h3","h2"])
            title = t.get_text(strip=True) if t else ""
        products[handle] = {
            "handle": handle,
            "title": title or handle.replace("-", " ").title(),
            "url": url
        }
    return list(products.values())

def try_collection_json(session):
    # Best effort; niet alle Shopify-shops laten dit toe
    out = {}
    page = 1
    while page <= 5:
        url = f"{COLLECTION_URL}/products.json?limit=250&page={page}"
        r = session.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            break
        data = r.json()
        arr = data.get("products", [])
        if not arr:
            break
        for p in arr:
            handle = p.get("handle")
            if not handle:
                continue
            out[handle] = {
                "handle": handle,
                "title": p.get("title", handle.replace("-", " ").title()),
                "url": f"https://hallyusuperstore.com/products/{handle}"
            }
        page += 1
    return list(out.values())

def fetch_once(session, cache):
    # Gebruik ETag/Last-Modified voor zuinige checks
    headers = dict(HEADERS)
    if cache.get("etag"):
        headers["If-None-Match"] = cache["etag"]
    if cache.get("last_modified"):
        headers["If-Modified-Since"] = cache["last_modified"]

    r = session.get(COLLECTION_URL, headers=headers, timeout=30)
    if r.status_code == 304:
        return None, False  # niets veranderd
    r.raise_for_status()

    cache["etag"] = r.headers.get("ETag") or cache.get("etag")
    cache["last_modified"] = r.headers.get("Last-Modified") or cache.get("last_modified")

    products_html = parse_products_from_html(r.text)
    # JSON-fallback proberen (optioneel; kan je uitzetten als je zuiniger wilt zijn)
    try:
        products_json = try_collection_json(session)
    except Exception:
        products_json = []
    merged = {}
    for p in products_html + products_json:
        merged[p["handle"]] = p
    return list(merged.values()), True

def main():
    session = requests.Session()
    seen = load_seen()
    cache = {"etag": None, "last_modified": None}
    backoff = INTERVAL_SEC

    print(f"[INFO] Watcher gestart. Interval: {INTERVAL_SEC}s (Amsterdam-tijd). Ctrl+C om te stoppen.")
    while True:
        try:
            products, changed = fetch_once(session, cache)
            if changed and products:
                current = {p["handle"] for p in products}
                new_handles = current - seen
                if new_handles:
                    new_items = [p for p in products if p["handle"] in new_handles]
                    lines = ["🆕 Nieuwe ATEEZ producten op HallyuSuperstore:"]
                    for p in sorted(new_items, key=lambda x: x["title"].lower()):
                        lines.append(f"• {p['title']} → {p['url']}")
                    send_telegram("\n".join(lines))
                    seen |= current
                    save_seen(seen)
                else:
                    print("[OK] Geen nieuwe producten (wel gewijzigde pagina).")
            else:
                print("[OK] Geen wijziging gedetecteerd.")
            # succes → reset backoff
            time.sleep(INTERVAL_SEC)
            backoff = INTERVAL_SEC
        except KeyboardInterrupt:
            print("\n[INFO] Gestopt door gebruiker.")
            break
        except Exception as e:
            print(f"[WARN] Fout: {e} — backoff...")
            time.sleep(backoff)
            backoff = min(int(backoff * 2), MAX_BACKOFF_SEC)

if __name__ == "__main__":
    main()

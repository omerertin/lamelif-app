import csv
import os
import re
import sqlite3
from dataclasses import dataclass, asdict
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

DB_FILE = "products.db"
CATALOG_CSV = "productsVariants.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )
}
TIMEOUT = 15
MAX_SEARCH_URLS = 20


@dataclass
class ProductImageResult:
    code: str
    title: str
    image_url: str
    page_url: str
    source: str = "live"


def normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", str(code).strip()).upper()


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS master_products (
            code TEXT PRIMARY KEY,
            product_name TEXT,
            product_id TEXT,
            category_name TEXT,
            price TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            title TEXT,
            image_url TEXT NOT NULL,
            page_url TEXT NOT NULL,
            source TEXT DEFAULT 'live',
            UNIQUE(code, image_url, page_url)
        )
    """)

    conn.commit()
    conn.close()


def import_master_catalog():
    if not os.path.exists(CATALOG_CSV):
        print(f"UYARI: {CATALOG_CSV} bulunamadı. Katalog import edilmedi.")
        return

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    with open(CATALOG_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        seen = set()

        for row in reader:
            code = normalize_code(row.get("Ürün Model Kodu", ""))
            if not code or code in seen:
                continue
            seen.add(code)

            cur.execute("""
                INSERT OR REPLACE INTO master_products
                (code, product_name, product_id, category_name, price)
                VALUES (?, ?, ?, ?, ?)
            """, (
                code,
                str(row.get("Ürün Adı", "")).strip(),
                str(row.get("Ürün ID", "")).strip(),
                str(row.get("Kategori Adı", "")).strip(),
                str(row.get("Fiyat", "")).strip(),
            ))

    conn.commit()
    conn.close()
    print("Katalog import edildi.")


def get_master_product(code: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        SELECT code, product_name, product_id, category_name, price
        FROM master_products
        WHERE code=?
    """, (code,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "code": row[0],
        "product_name": row[1],
        "product_id": row[2],
        "category_name": row[3],
        "price": row[4],
    }


def get_cached_images(code: str) -> list[ProductImageResult]:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        SELECT code, title, image_url, page_url, source
        FROM product_images
        WHERE code=?
        ORDER BY id DESC
    """, (code,))
    rows = cur.fetchall()
    conn.close()

    return [
        ProductImageResult(
            code=row[0],
            title=row[1] or code,
            image_url=row[2],
            page_url=row[3],
            source=row[4] or "live",
        )
        for row in rows
    ]


def save_image(item: ProductImageResult):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO product_images
        (code, title, image_url, page_url, source)
        VALUES (?, ?, ?, ?, ?)
    """, (item.code, item.title, item.image_url, item.page_url, item.source))
    conn.commit()
    conn.close()


def fetch(url: str, params: dict | None = None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r
    except Exception:
        return None


def clean_url(url: str) -> str:
    if not url:
        return url
    url = unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    return url


def is_lamelif_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return "lamelif.com" in host
    except Exception:
        return False


def dedupe(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        if not item:
            continue
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_real_ddg_link(href: str) -> str:
    href = clean_url(href)
    parsed = urlparse(href)

    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        q = parse_qs(parsed.query)
        uddg = q.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)

    return href


def ddg_search_urls(code: str, product_name: str) -> list[str]:
    queries = [
        f'site:lamelif.com "{code}"',
        f'site:lamelif.com "{product_name}" "{code}"',
        f'site:lamelif.com "Model kodu: {code}"',
        f'lamelif "{product_name}" "{code}"',
        f'lamelif "{code}"',
    ]

    urls = []

    for query in queries:
        resp = fetch(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
        if not resp:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a, a[href]"):
            href = a.get("href")
            if not href:
                continue

            real = extract_real_ddg_link(href)
            if is_lamelif_url(real):
                cleaned = real.split("#")[0]
                if "?" in cleaned:
                    cleaned = cleaned.split("?")[0]
                urls.append(cleaned)

    return dedupe(urls)[:MAX_SEARCH_URLS]


def lamelif_internal_search_urls(code: str, product_name: str) -> list[str]:
    search_terms = [
        code,
        f"{product_name} {code}".strip(),
        product_name,
    ]

    urls = []

    for term in search_terms:
        if not term:
            continue

        for base in [
            "https://www.lamelif.com/arama",
            "https://www.lamelif.com/search",
        ]:
            resp = fetch(base, params={"q": term})
            if not resp:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select("a[href]"):
                href = a.get("href")
                if not href:
                    continue

                full = clean_url(urljoin("https://www.lamelif.com", href))
                if not is_lamelif_url(full):
                    continue

                low = full.lower()
                if any(skip in low for skip in [
                    "/arama", "/search", "/sepet", "/uye", "/account", "/cart",
                    "/iletisim", "/blog", "/yardim"
                ]):
                    continue

                cleaned = full.split("#")[0]
                if "?" in cleaned:
                    cleaned = cleaned.split("?")[0]

                urls.append(cleaned)

    return dedupe(urls)[:MAX_SEARCH_URLS]


def extract_code_from_html(html: str) -> str | None:
    text = html.upper()

    patterns = [
        r"MODEL\s*KODU\s*[:.]?\s*([A-Z0-9]+)",
        r"MODELKODU\s*[:.]?\s*([A-Z0-9]+)",
        r"REF\.\s*([A-Z0-9]+)",
        r"REF\s*[:.]?\s*([A-Z0-9]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip().upper()

    return None


def extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)

    if soup.title:
        return soup.title.get_text(" ", strip=True)

    return ""


def extract_candidate_images(soup: BeautifulSoup, base_url: str) -> list[str]:
    images = []

    for selector in [
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'meta[property="twitter:image"]',
    ]:
        for tag in soup.select(selector):
            content = tag.get("content")
            if content:
                images.append(content)

    for img in soup.select("img"):
        for attr in ["data-zoom-image", "data-src", "src", "data-lazy-src", "data-original"]:
            value = img.get(attr)
            if value:
                images.append(value)

    cleaned = []
    seen = set()

    for raw in images:
        full = urljoin(base_url, clean_url(raw))
        low = full.lower()

        if any(skip in low for skip in [
            "logo", "icon", "sprite", "banner", "payment", "bank", "favicon"
        ]):
            continue

        if full not in seen:
            seen.add(full)
            cleaned.append(full)

    return cleaned


def pick_best_image(images: list[str]) -> str | None:
    if not images:
        return None

    def score(url: str) -> int:
        low = url.lower()
        s = 0
        for token in [
            "uploads", "urun", "product", "zoom", "large", "original",
            ".jpg", ".jpeg", ".png", ".webp"
        ]:
            if token in low:
                s += 5
        return s

    images.sort(key=score, reverse=True)
    return images[0]


def inspect_product_page(url: str) -> ProductImageResult | None:
    resp = fetch(url)
    if not resp:
        return None

    code = extract_code_from_html(resp.text)
    if not code:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    title = extract_title(soup) or url
    image_url = pick_best_image(extract_candidate_images(soup, url))

    if not image_url:
        return None

    return ProductImageResult(
        code=code,
        title=title,
        image_url=image_url,
        page_url=url,
        source="live",
    )


def live_search_for_code(code: str, product_name: str) -> list[ProductImageResult]:
    urls = []
    urls.extend(lamelif_internal_search_urls(code, product_name))
    urls.extend(ddg_search_urls(code, product_name))
    urls = dedupe(urls)

    found = []
    for url in urls:
        item = inspect_product_page(url)
        if item and item.code == code:
            found.append(item)

    # aynı kayıtları ele
    unique = []
    seen = set()
    for item in found:
        key = (item.code, item.image_url, item.page_url)
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/search")
def api_search():
    code = normalize_code(request.args.get("code", ""))

    if not code:
        return jsonify({"ok": False, "error": "Ürün kodu boş olamaz."}), 400

    master = get_master_product(code)
    if not master:
        return jsonify({
            "ok": True,
            "code": code,
            "results": [],
            "message": "Bu kod ürün listesinde yok."
        })

    cached = get_cached_images(code)
    if cached:
        return jsonify({
            "ok": True,
            "code": code,
            "product_name": master["product_name"],
            "results": [asdict(x) for x in cached],
            "from_cache": True
        })

    found = live_search_for_code(code, master["product_name"])
    if found:
        for item in found:
            save_image(item)

        return jsonify({
            "ok": True,
            "code": code,
            "product_name": master["product_name"],
            "results": [asdict(x) for x in found],
            "from_cache": False
        })

    return jsonify({
        "ok": True,
        "code": code,
        "product_name": master["product_name"],
        "results": [],
        "message": "Kod katalogda var ama görsel henüz bulunamadı. Ürün canlı sitede indekslenmemiş veya kaldırılmış olabilir."
    })


@app.post("/api/teach")
def api_teach():
    data = request.get_json(force=True)

    code = normalize_code(data.get("code", ""))
    title = str(data.get("title", "")).strip() or code
    page_url = str(data.get("page_url", "")).strip()
    image_url = str(data.get("image_url", "")).strip()

    if not code or not page_url or not image_url:
        return jsonify({
            "ok": False,
            "error": "code, page_url ve image_url gerekli"
        }), 400

    master = get_master_product(code)
    if not master:
        return jsonify({
            "ok": False,
            "error": "Bu kod katalogda yok"
        }), 400

    item = ProductImageResult(
        code=code,
        title=title,
        image_url=image_url,
        page_url=page_url,
        source="manual",
    )
    save_image(item)

    return jsonify({
        "ok": True,
        "saved": asdict(item)
    })


@app.get("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")


@app.get("/service-worker.js")
def sw():
    return send_from_directory("static", "service-worker.js")


if __name__ == "__main__":
    init_db()
    import_master_catalog()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

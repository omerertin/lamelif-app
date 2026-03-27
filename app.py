import os
import re
import sqlite3
from dataclasses import dataclass, asdict
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs, unquote
from html import unescape

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)
DB_FILE = "products.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )
}
TIMEOUT = 15


@dataclass
class ProductResult:
    code: str
    title: str
    image_url: str
    page_url: str
    source: str = "live"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            code TEXT,
            title TEXT,
            image_url TEXT,
            page_url TEXT,
            source TEXT DEFAULT 'live'
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_code ON products(code)")
    conn.commit()
    conn.close()


def get_cached_products(code: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        "SELECT code, title, image_url, page_url, source FROM products WHERE code=?",
        (code,)
    )
    rows = cur.fetchall()
    conn.close()
    return [
        ProductResult(
            code=row[0],
            title=row[1],
            image_url=row[2],
            page_url=row[3],
            source=row[4],
        )
        for row in rows
    ]


def save_product(item: ProductResult):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM products
        WHERE code=? AND page_url=? AND image_url=?
    """, (item.code, item.page_url, item.image_url))
    cur.execute("""
        INSERT INTO products (code, title, image_url, page_url, source)
        VALUES (?, ?, ?, ?, ?)
    """, (item.code, item.title, item.image_url, item.page_url, item.source))
    conn.commit()
    conn.close()


def normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", code.strip()).upper()


def fetch(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
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


def extract_real_ddg_link(href: str) -> str:
    href = clean_url(href)
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        q = parse_qs(parsed.query)
        uddg = q.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)
    return href


def dedupe(items):
    out = []
    seen = set()
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def ddg_urls_for_code(code: str):
    queries = [
        f'site:lamelif.com "{code}"',
        f'site:lamelif.com "Model kodu: {code}"',
        f"site:lamelif.com {code}",
        f"lamelif {code}",
    ]
    urls = []

    for q in queries:
        resp = fetch(f"https://html.duckduckgo.com/html/?q={quote_plus(q)}")
        if not resp:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a, a[href]"):
            href = a.get("href")
            if not href:
                continue
            real = extract_real_ddg_link(href)
            if is_lamelif_url(real):
                urls.append(real.split("?")[0])

    return dedupe(urls)


def extract_code_from_html(html: str) -> str | None:
    text = html.upper()

    patterns = [
        r"MODEL\s*KODU\s*[:.]?\s*([A-Z0-9]+)",
        r"REF\.\s*([A-Z0-9]+)",
        r"REF\s*[:.]?\s*([A-Z0-9]+)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip().upper()

    return None


def extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    if soup.title:
        return soup.title.get_text(" ", strip=True)
    return ""


def extract_best_image(soup: BeautifulSoup, base_url: str) -> str | None:
    candidates = []

    for selector in [
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'meta[property="twitter:image"]',
    ]:
        for tag in soup.select(selector):
            content = tag.get("content")
            if content:
                candidates.append(content)

    for img in soup.select("img"):
        for attr in ["data-zoom-image", "data-src", "src", "data-lazy-src"]:
            value = img.get(attr)
            if value:
                candidates.append(value)

    cleaned = []
    for c in candidates:
        full = urljoin(base_url, clean_url(c))
        low = full.lower()
        if any(x in low for x in ["logo", "icon", "sprite", "banner", "payment", "bank"]):
            continue
        cleaned.append(full)

    cleaned = dedupe(cleaned)
    if not cleaned:
        return None

    def score(url: str):
        low = url.lower()
        s = 0
        for token in ["product", "urun", "zoom", "large", ".jpg", ".jpeg", ".png", ".webp"]:
            if token in low:
                s += 5
        return s

    cleaned.sort(key=score, reverse=True)
    return cleaned[0]


def inspect_product_page(url: str):
    resp = fetch(url)
    if not resp:
        return None

    code = extract_code_from_html(resp.text)
    if not code:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    title = extract_title(soup) or url
    image_url = extract_best_image(soup, url)

    if not image_url:
        return None

    return ProductResult(
        code=code,
        title=title,
        image_url=image_url,
        page_url=url,
        source="live"
    )


def live_search(code: str):
    urls = ddg_urls_for_code(code)
    results = []

    for url in urls:
        item = inspect_product_page(url)
        if item and item.code == code:
            results.append(item)

    return results


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/search")
def api_search():
    code = normalize_code(request.args.get("code", ""))
    if not code:
        return jsonify({"ok": False, "error": "Ürün kodu boş olamaz."}), 400

    cached = get_cached_products(code)
    if cached:
        return jsonify({
            "ok": True,
            "code": code,
            "results": [asdict(x) for x in cached],
            "from_cache": True
        })

    found = live_search(code)
    if found:
        for item in found:
            save_product(item)

        return jsonify({
            "ok": True,
            "code": code,
            "results": [asdict(x) for x in found],
            "from_cache": False
        })

    return jsonify({
        "ok": True,
        "code": code,
        "results": [],
        "message": "Bu kod için sonuç bulunamadı."
    })


@app.get("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")


@app.get("/service-worker.js")
def sw():
    return send_from_directory("static", "service-worker.js")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

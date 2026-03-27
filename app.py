import json
import os
import re
from dataclasses import dataclass, asdict
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )
}
TIMEOUT = 12
MAX_RESULTS = 6

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")


@dataclass
class SearchResult:
    title: str
    page_url: str
    image_url: str | None = None
    score: int = 0
    source: str = ""
    matched_code: bool = False


def normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", code.strip()).upper()


def is_lamelif_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return "lamelif.com" in host
    except Exception:
        return False


def clean_url(url: str) -> str:
    if not url:
        return url
    url = unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        url = urljoin("https://www.lamelif.com", url)
    return url


def extract_real_ddg_link(href: str) -> str:
    href = clean_url(href)
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        q = parse_qs(parsed.query)
        uddg = q.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)
    return href


def fetch(url: str) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp
    except Exception:
        return None


def google_web_search(code: str) -> list[str]:
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": f'site:lamelif.com "{code}"',
        "num": MAX_RESULTS,
        "safe": "off",
    }
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        urls = []
        for item in data.get("items", []):
            link = item.get("link")
            if link and is_lamelif_url(link):
                urls.append(link)
        return urls
    except Exception:
        return []


def google_image_search(code: str) -> list[str]:
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": f'site:lamelif.com "{code}"',
        "searchType": "image",
        "num": 5,
        "safe": "off",
    }
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return [item.get("link") for item in data.get("items", []) if item.get("link")]
    except Exception:
        return []


def ddg_search(code: str) -> list[str]:
    query = f'site:lamelif.com "{code}"'
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    resp = fetch(url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    urls = []
    for a in soup.select("a.result__a, a[href]"):
        href = a.get("href")
        if not href:
            continue
        real = extract_real_ddg_link(href)
        if is_lamelif_url(real):
            urls.append(real)
    deduped = []
    seen = set()
    for u in urls:
        base = u.split("?")[0]
        if base not in seen:
            seen.add(base)
            deduped.append(base)
    return deduped[:MAX_RESULTS]


def extract_jsonld_images(soup: BeautifulSoup) -> list[str]:
    images = []
    for script in soup.select('script[type="application/ld+json"]'):
        text = script.string or script.get_text(strip=True)
        if not text:
            continue
        try:
            payload = json.loads(text)
            stack = payload if isinstance(payload, list) else [payload]
            for item in stack:
                if isinstance(item, dict):
                    img = item.get("image")
                    if isinstance(img, str):
                        images.append(img)
                    elif isinstance(img, list):
                        images.extend([x for x in img if isinstance(x, str)])
        except Exception:
            continue
    return images


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

    images.extend(extract_jsonld_images(soup))

    for img in soup.select("img"):
        for attr in ["data-zoom-image", "data-src", "src", "data-lazy-src"]:
            value = img.get(attr)
            if value:
                images.append(value)

    cleaned = []
    seen = set()
    for raw in images:
        url = clean_url(urljoin(base_url, raw))
        if not url.startswith("http"):
            continue
        lower = url.lower()
        if any(skip in lower for skip in ["logo", "icon", "sprite", "banner", "payment", "bank"]):
            continue
        if url not in seen:
            seen.add(url)
            cleaned.append(url)
    return cleaned


def extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return title


def page_score(html: str, code: str, url: str) -> tuple[int, bool]:
    haystack = html.upper()
    score = 0
    matched = False
    if code in haystack:
        score += 100
        matched = True
    if re.search(rf"MODEL\s*KODU\s*[:.]?\s*{re.escape(code)}", haystack):
        score += 80
    if re.search(rf"REF\.\s*{re.escape(code)}", haystack):
        score += 70
    if code in url.upper():
        score += 15
    return score, matched


def inspect_product_page(url: str, code: str) -> SearchResult | None:
    resp = fetch(url)
    if not resp:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    title = extract_title(soup) or url
    score, matched = page_score(resp.text, code, url)
    images = extract_candidate_images(soup, url)
    image_url = images[0] if images else None
    return SearchResult(
        title=title,
        page_url=url,
        image_url=image_url,
        score=score,
        source="lamelif sayfası",
        matched_code=matched,
    )


def search_product(code: str) -> list[SearchResult]:
    code = normalize_code(code)
    urls = google_web_search(code)
    if not urls:
        urls = ddg_search(code)

    results: list[SearchResult] = []
    for url in urls:
        result = inspect_product_page(url, code)
        if result:
            results.append(result)

    if not results:
        for img in google_image_search(code):
            results.append(
                SearchResult(
                    title=f"Google görsel sonucu - {code}",
                    page_url=f"https://www.google.com/search?tbm=isch&q={quote_plus('site:lamelif.com ' + code)}",
                    image_url=img,
                    score=40,
                    source="google görseller api",
                    matched_code=False,
                )
            )

    results.sort(key=lambda x: (x.score, x.matched_code, bool(x.image_url)), reverse=True)
    return results[:5]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/search")
def api_search():
    code = normalize_code(request.args.get("code", ""))
    if not code:
        return jsonify({"ok": False, "error": "Ürün kodu boş olamaz."}), 400

    results = search_product(code)
    if not results:
        return jsonify(
            {
                "ok": True,
                "code": code,
                "results": [],
                "message": "Eşleşen görsel bulunamadı. Kodun doğru yazıldığını kontrol et.",
            }
        )

    return jsonify({"ok": True, "code": code, "results": [asdict(r) for r in results]})


@app.get("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")


@app.get("/service-worker.js")
def sw():
    return send_from_directory("static", "service-worker.js")


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

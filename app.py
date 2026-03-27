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
app.config["JSON_AS_ASCII"] = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )
}
TIMEOUT = 15
MAX_RESULTS = 10

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


def fetch(url: str, params: dict | None = None) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp
    except Exception:
        return None


def dedupe_urls(urls: list[str]) -> list[str]:
    cleaned = []
    seen = set()
    for u in urls:
        if not u:
            continue
        base = u.split("#")[0]
        if "?" in base:
            base = base.split("?")[0]
        if base not in seen:
            seen.add(base)
            cleaned.append(base)
    return cleaned


def google_web_search_variants(code: str) -> list[str]:
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    queries = [
        f'site:lamelif.com "{code}"',
        f'site:lamelif.com "Model kodu: {code}"',
        f"site:lamelif.com {code}",
        f"lamelif {code}",
    ]

    found_urls = []

    for query in queries:
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CSE_ID,
            "q": query,
            "num": 10,
            "safe": "off",
        }
        resp = fetch("https://www.googleapis.com/customsearch/v1", params=params)
        if not resp:
            continue

        try:
            data = resp.json()
        except Exception:
            continue

        for item in data.get("items", []):
            link = item.get("link")
            if link and is_lamelif_url(link):
                found_urls.append(link)

    return dedupe_urls(found_urls)[:15]


def google_image_search(code: str) -> list[str]:
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    queries = [
        f'site:lamelif.com "{code}"',
        f'site:lamelif.com "Model kodu: {code}"',
        f"lamelif {code}",
    ]

    images = []

    for query in queries:
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CSE_ID,
            "q": query,
            "searchType": "image",
            "num": 5,
            "safe": "off",
        }
        resp = fetch("https://www.googleapis.com/customsearch/v1", params=params)
        if not resp:
            continue

        try:
            data = resp.json()
        except Exception:
            continue

        for item in data.get("items", []):
            link = item.get("link")
            if link:
                images.append(link)

    return dedupe_urls(images)[:8]


def ddg_search_variants(code: str) -> list[str]:
    queries = [
        f'site:lamelif.com "{code}"',
        f'site:lamelif.com "Model kodu: {code}"',
        f'site:lamelif.com "model kodu {code}"',
        f"site:lamelif.com {code}",
        f"lamelif {code}",
    ]

    all_urls = []
    seen = set()

    for query in queries:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        resp = fetch(url)
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
                if cleaned not in seen:
                    seen.add(cleaned)
                    all_urls.append(cleaned)

    return all_urls[:15]


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
                if not isinstance(item, dict):
                    continue

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

    if soup.title:
        return soup.title.get_text(" ", strip=True)

    return ""


def page_score(html: str, code: str, url: str) -> tuple[int, bool]:
    haystack = html.upper()
    score = 0
    matched = False

    if code in haystack:
        score += 120
        matched = True

    patterns = [
        rf"MODEL\s*KODU\s*[:.]?\s*{re.escape(code)}",
        rf"MODELKODU\s*[:.]?\s*{re.escape(code)}",
        rf"REF\.\s*{re.escape(code)}",
        rf"REF\s*[:.]?\s*{re.escape(code)}",
        rf"\b{re.escape(code)}\b",
    ]

    for pattern in patterns:
        if re.search(pattern, haystack):
            score += 60

    if code in url.upper():
        score += 20

    return score, matched


def pick_best_image(images: list[str]) -> str | None:
    if not images:
        return None

    preferred_keywords = [
        "urun",
        "product",
        "large",
        "zoom",
        "original",
        "cdn",
        "upload",
    ]

    def img_score(url: str) -> int:
        score = 0
        lower = url.lower()
        for keyword in preferred_keywords:
            if keyword in lower:
                score += 5
        if lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".png") or ".webp" in lower:
            score += 3
        return score

    images = sorted(images, key=img_score, reverse=True)
    return images[0]


def inspect_product_page(url: str, code: str) -> SearchResult | None:
    resp = fetch(url)
    if not resp:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    title = extract_title(soup) or url
    score, matched = page_score(resp.text, code, url)
    images = extract_candidate_images(soup, url)
    image_url = pick_best_image(images)

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

    urls = []

    google_urls = google_web_search_variants(code)
    if google_urls:
        urls.extend(google_urls)

    ddg_urls = ddg_search_variants(code)
    if ddg_urls:
        urls.extend(ddg_urls)

    urls = dedupe_urls(urls)

    results: list[SearchResult] = []
    for url in urls:
        result = inspect_product_page(url, code)
        if result and (result.matched_code or result.image_url):
            results.append(result)

    if len(results) < 2:
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

    results.sort(
        key=lambda x: (
            x.matched_code,
            x.score,
            bool(x.image_url),
        ),
        reverse=True,
    )

    final_results = []
    seen = set()
    for item in results:
        key = (item.page_url, item.image_url)
        if key not in seen:
            seen.add(key)
            final_results.append(item)

    return final_results[:8]


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

    return jsonify(
        {
            "ok": True,
            "code": code,
            "results": [asdict(r) for r in results],
        }
    )


@app.get("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")


@app.get("/service-worker.js")
def sw():
    return send_from_directory("static", "service-worker.js")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

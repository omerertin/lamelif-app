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

TIMEOUT = 18
MAX_RESULTS = 12

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
    archived: bool = False
    snapshot_time: str | None = None


def normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", code.strip()).upper()


def fetch(url: str, params: dict | None = None) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp
    except Exception:
        return None


def clean_url(url: str) -> str:
    if not url:
        return url
    url = unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        url = urljoin("https://www.lamelif.com", url)
    return url


def is_lamelif_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return "lamelif.com" in host
    except Exception:
        return False


def dedupe_urls(urls: list[str]) -> list[str]:
    out = []
    seen = set()
    for u in urls:
        if not u:
            continue
        x = u.split("#")[0]
        if x not in seen:
            seen.add(x)
            out.append(x)
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


def google_web_search_variants(code: str) -> list[str]:
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    queries = [
        f'site:lamelif.com "{code}"',
        f'site:lamelif.com "Model kodu: {code}"',
        f'site:lamelif.com "model kodu {code}"',
        f"site:lamelif.com {code}",
        f"lamelif {code}",
    ]

    urls = []
    for query in queries:
        resp = fetch(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CSE_ID,
                "q": query,
                "num": 10,
                "safe": "off",
            },
        )
        if not resp:
            continue
        try:
            data = resp.json()
        except Exception:
            continue

        for item in data.get("items", []):
            link = item.get("link")
            if link and is_lamelif_url(link):
                urls.append(link)

    return dedupe_urls(urls)[:20]


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
        resp = fetch(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CSE_ID,
                "q": query,
                "searchType": "image",
                "num": 5,
                "safe": "off",
            },
        )
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

    return dedupe_urls(images)[:10]


def ddg_search_variants(code: str) -> list[str]:
    queries = [
        f'site:lamelif.com "{code}"',
        f'site:lamelif.com "Model kodu: {code}"',
        f'site:lamelif.com "model kodu {code}"',
        f"site:lamelif.com {code}",
        f"lamelif {code}",
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
                urls.append(real.split("?")[0])

    return dedupe_urls(urls)[:20]


def lamelif_site_search(code: str) -> list[str]:
    # Lamelif iç arama URL yapısı zamanla değişebilir, o yüzden birkaç varyasyon deniyoruz
    search_urls = [
        f"https://www.lamelif.com/arama?q={quote_plus(code)}",
        f"https://www.lamelif.com/arama?q={quote_plus('Model kodu ' + code)}",
        f"https://www.lamelif.com/arama?q={quote_plus('Ref ' + code)}",
        f"https://www.lamelif.com/search?q={quote_plus(code)}",
        f"https://www.lamelif.com/search?q={quote_plus('Model kodu ' + code)}",
    ]

    urls = []
    for search_url in search_urls:
        resp = fetch(search_url)
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
            if any(x in low for x in ["/arama", "/search", "/sepet", "/uye", "/account", "/cart"]):
                continue

            urls.append(full.split("?")[0])

    return dedupe_urls(urls)[:20]


def extract_jsonld_images(soup: BeautifulSoup) -> list[str]:
    images = []
    for script in soup.select('script[type="application/ld+json"]'):
        text = script.string or script.get_text(strip=True)
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue

        stack = data if isinstance(data, list) else [data]
        for item in stack:
            if not isinstance(item, dict):
                continue
            img = item.get("image")
            if isinstance(img, str):
                images.append(img)
            elif isinstance(img, list):
                images.extend([x for x in img if isinstance(x, str)])

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
        for attr in ["data-zoom-image", "data-src", "src", "data-lazy-src", "data-original"]:
            value = img.get(attr)
            if value:
                images.append(value)

    cleaned = []
    seen = set()
    for raw in images:
        u = clean_url(urljoin(base_url, raw))
        if not u.startswith("http"):
            continue
        low = u.lower()
        if any(skip in low for skip in ["logo", "icon", "sprite", "banner", "payment", "bank"]):
            continue
        if u not in seen:
            seen.add(u)
            cleaned.append(u)

    return cleaned


def pick_best_image(images: list[str]) -> str | None:
    if not images:
        return None

    def score(url: str) -> int:
        s = 0
        low = url.lower()
        for token in ["product", "urun", "zoom", "large", "original", "cdn", "upload", ".jpg", ".jpeg", ".png", ".webp"]:
            if token in low:
                s += 5
        return s

    return sorted(images, key=score, reverse=True)[0]


def extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    if soup.title:
        return soup.title.get_text(" ", strip=True)
    return ""


def page_score(html: str, code: str, url: str) -> tuple[int, bool]:
    hay = html.upper()
    score = 0
    matched = False

    if code in hay:
        score += 150
        matched = True

    patterns = [
        rf"MODEL\s*KODU\s*[:.]?\s*{re.escape(code)}",
        rf"MODELKODU\s*[:.]?\s*{re.escape(code)}",
        rf"REF\.\s*{re.escape(code)}",
        rf"REF\s*[:.]?\s*{re.escape(code)}",
        rf"\b{re.escape(code)}\b",
    ]

    for p in patterns:
        if re.search(p, hay):
            score += 70

    if code in url.upper():
        score += 25

    return score, matched


def inspect_html_result(html: str, base_url: str, code: str, source: str, archived: bool = False, snapshot_time: str | None = None) -> SearchResult:
    soup = BeautifulSoup(html, "html.parser")
    title = extract_title(soup) or base_url
    score, matched = page_score(html, code, base_url)
    images = extract_candidate_images(soup, base_url)
    image_url = pick_best_image(images)

    return SearchResult(
        title=title,
        page_url=base_url,
        image_url=image_url,
        score=score,
        source=source,
        matched_code=matched,
        archived=archived,
        snapshot_time=snapshot_time,
    )


def inspect_product_page(url: str, code: str) -> SearchResult | None:
    resp = fetch(url)
    if not resp:
        return None
    return inspect_html_result(resp.text, url, code, "lamelif canlı sayfa")


def wayback_available(url: str) -> dict | None:
    resp = fetch("https://archive.org/wayback/available", params={"url": url})
    if not resp:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def get_wayback_closest_url(url: str) -> tuple[str | None, str | None]:
    data = wayback_available(url)
    if not data:
        return None, None

    closest = data.get("archived_snapshots", {}).get("closest", {})
    if closest.get("available") and closest.get("url"):
        return closest.get("url"), closest.get("timestamp")
    return None, None


def wayback_cdx_search(code: str) -> list[tuple[str, str]]:
    """
    CDX ile lamelif.com altında kod geçen olası URL'leri arar.
    Dönüş: [(snapshot_url, timestamp), ...]
    """
    queries = [
        f"https://www.lamelif.com/*{code}*",
        f"https://lamelif.com/*{code}*",
    ]

    snapshot_pairs = []

    for q in queries:
        resp = fetch(
            "https://web.archive.org/cdx/search/cdx",
            params={
                "url": q,
                "output": "json",
                "fl": "timestamp,original,statuscode,mimetype",
                "filter": ["statuscode:200", "mimetype:text/html"],
                "limit": "25",
                "from": "2018",
            },
        )
        if not resp:
            continue

        try:
            data = resp.json()
        except Exception:
            continue

        if not isinstance(data, list) or len(data) < 2:
            continue

        for row in data[1:]:
            if not isinstance(row, list) or len(row) < 2:
                continue
            timestamp, original = row[0], row[1]
            if "lamelif.com" not in original:
                continue
            snapshot_url = f"https://web.archive.org/web/{timestamp}/{original}"
            snapshot_pairs.append((snapshot_url, timestamp))

    # ek olarak canlı arama sonuçlarından çıkan URL'lerin archive closest'ini de deneyelim
    return snapshot_pairs[:25]


def search_archive_for_urls(urls: list[str], code: str) -> list[SearchResult]:
    results = []

    for url in urls:
        snapshot_url, ts = get_wayback_closest_url(url)
        if not snapshot_url:
            continue

        resp = fetch(snapshot_url)
        if not resp:
            continue

        item = inspect_html_result(
            resp.text,
            snapshot_url,
            code,
            "wayback arşivi",
            archived=True,
            snapshot_time=ts,
        )
        # Arşiv sonucuysa ve görsel ya da kod geçtiyse kaydet
        if item.image_url or item.matched_code:
            item.score += 40
            results.append(item)

    return results


def search_archive_by_code(code: str) -> list[SearchResult]:
    results = []
    pairs = wayback_cdx_search(code)

    for snapshot_url, ts in pairs:
        resp = fetch(snapshot_url)
        if not resp:
            continue

        item = inspect_html_result(
            resp.text,
            snapshot_url,
            code,
            "wayback cdx",
            archived=True,
            snapshot_time=ts,
        )

        if item.matched_code or item.image_url:
            item.score += 60
            results.append(item)

    return results


def rank_results(results: list[SearchResult]) -> list[SearchResult]:
    results.sort(
        key=lambda x: (
            x.matched_code,
            bool(x.image_url),
            not x.archived,  # canlı sayfa varsa önce
            x.score,
        ),
        reverse=True,
    )

    final = []
    seen = set()
    for r in results:
        key = (r.page_url, r.image_url)
        if key in seen:
            continue
        seen.add(key)
        final.append(r)

    return final[:10]


def search_product(code: str) -> list[SearchResult]:
    code = normalize_code(code)

    # 1) canlı tara
    live_urls = []
    live_urls.extend(lamelif_site_search(code))
    live_urls.extend(google_web_search_variants(code))
    live_urls.extend(ddg_search_variants(code))
    live_urls = dedupe_urls(live_urls)

    live_results = []
    for url in live_urls:
        item = inspect_product_page(url, code)
        if item and (item.matched_code or item.image_url):
            live_results.append(item)

    # canlı sonuç iyiyse direkt dön
    ranked_live = rank_results(live_results)
    if ranked_live:
        return ranked_live

    # 2) canlı URL'lerin arşiv kopyasına bak
    archive_results = search_archive_for_urls(live_urls, code)

    # 3) kod bazlı doğrudan wayback taraması
    if len(archive_results) < 3:
        archive_results.extend(search_archive_by_code(code))

    all_results = rank_results(archive_results)
    if all_results:
        return all_results

    # 4) son çare: görsel arama
    image_results = []
    for img in google_image_search(code):
        image_results.append(
            SearchResult(
                title=f"Google görsel sonucu - {code}",
                page_url=f"https://www.google.com/search?tbm=isch&q={quote_plus('site:lamelif.com ' + code)}",
                image_url=img,
                score=35,
                source="google görseller api",
                matched_code=False,
                archived=False,
                snapshot_time=None,
            )
        )

    return rank_results(image_results)


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
                "message": "Canlı sitede veya arşivlerde eşleşen görsel bulunamadı.",
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

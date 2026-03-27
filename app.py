import csv
import os
import re
from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

CATALOG_FILE = "productsVariants.csv"
PRODUCTS_BY_CODE = {}


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", normalize_text(code)).upper()


def load_catalog():
    global PRODUCTS_BY_CODE
    PRODUCTS_BY_CODE = {}

    if not os.path.exists(CATALOG_FILE):
        print(f"Katalog dosyası bulunamadı: {CATALOG_FILE}")
        return

    with open(CATALOG_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            code = normalize_code(row.get("Ürün Model Kodu", ""))
            if not code:
                continue

            item = {
                "code": code,
                "product_name": normalize_text(row.get("Ürün Adı", "")),
                "product_id": normalize_text(row.get("Ürün ID", "")),
                "category_name": normalize_text(row.get("Kategori Adı", "")),
                "price": normalize_text(row.get("Fiyat", "")),
                "color": normalize_text(row.get("Renk", "")),
                "size": normalize_text(row.get("Beden", "")),
                "barcode": normalize_text(row.get("Barkod", "")),
                "stock": normalize_text(row.get("Stok", "")),
                "image_url": "",
                "page_url": "",
                "source": "catalog",
            }

            PRODUCTS_BY_CODE.setdefault(code, []).append(item)

    print(f"Katalog yüklendi. Kod sayısı: {len(PRODUCTS_BY_CODE)}")


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/search")
def api_search():
    code = normalize_code(request.args.get("code", ""))

    if not code:
        return jsonify({
            "ok": False,
            "error": "Ürün kodu boş olamaz."
        }), 400

    items = PRODUCTS_BY_CODE.get(code, [])

    if not items:
        return jsonify({
            "ok": True,
            "found_in_catalog": False,
            "code": code,
            "results": [],
            "message": "Bu kod katalogda yok."
        })

    return jsonify({
        "ok": True,
        "found_in_catalog": True,
        "code": code,
        "count": len(items),
        "results": items,
        "message": f"Bu kod katalogda bulundu. {len(items)} kayıt var."
    })


@app.get("/api/reload")
def api_reload():
    load_catalog()
    return jsonify({
        "ok": True,
        "message": "Katalog yeniden yüklendi.",
        "code_count": len(PRODUCTS_BY_CODE)
    })


@app.get("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")


@app.get("/service-worker.js")
def sw():
    return send_from_directory("static", "service-worker.js")


@app.errorhandler(500)
def internal_error(e):
    return jsonify({
        "ok": False,
        "error": "Sunucu hatası oluştu"
    }), 500


if __name__ == "__main__":
    load_catalog()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

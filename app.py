import csv
import os
import re
import sqlite3
from dataclasses import dataclass, asdict
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DB_FILE = "products.db"
CATALOG_FILE = "productsVariants.csv"


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
            image_url TEXT,
            page_url TEXT,
            source TEXT DEFAULT 'cache'
        )
    """)

    conn.commit()
    conn.close()


def import_catalog():
    if not os.path.exists(CATALOG_FILE):
        print(f"Katalog dosyası bulunamadı: {CATALOG_FILE}")
        return

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    with open(CATALOG_FILE, "r", encoding="utf-8-sig", newline="") as f:
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
    print("Katalog içeri aktarıldı.")


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


def get_cached_images(code: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        SELECT title, image_url, page_url, source
        FROM product_images
        WHERE code=?
        ORDER BY id DESC
    """, (code,))
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "code": code,
            "title": row[0] or code,
            "image_url": row[1],
            "page_url": row[2],
            "source": row[3] or "cache",
        }
        for row in rows
    ]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/search")
def api_search():
    code = normalize_code(request.args.get("code", ""))

    if not code:
        return jsonify({"ok": False, "error": "Ürün kodu boş olamaz."}), 400

    product = get_master_product(code)
    if not product:
        return jsonify({
            "ok": True,
            "found_in_catalog": False,
            "code": code,
            "results": [],
            "message": "Bu kod katalogda yok."
        })

    cached_images = get_cached_images(code)

    # listedeki ürün mutlaka dönsün
    if cached_images:
        return jsonify({
            "ok": True,
            "found_in_catalog": True,
            "code": code,
            "product_name": product["product_name"],
            "product_id": product["product_id"],
            "category_name": product["category_name"],
            "price": product["price"],
            "results": cached_images,
            "message": "Ürün katalogda bulundu. Kayıtlı görseller gösteriliyor."
        })

    return jsonify({
        "ok": True,
        "found_in_catalog": True,
        "code": code,
        "product_name": product["product_name"],
        "product_id": product["product_id"],
        "category_name": product["category_name"],
        "price": product["price"],
        "results": [],
        "message": "Ürün katalogda bulundu ama bu ürün için henüz görsel kaydı yok."
    })


@app.errorhandler(500)
def internal_error(e):
    return jsonify({
        "ok": False,
        "error": "Sunucu hatası oluştu"
    }), 500


if __name__ == "__main__":
    init_db()
    import_catalog()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

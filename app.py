import os
import re
import sqlite3
from dataclasses import dataclass, asdict
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DB_FILE = "products.db"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# ---------------- DB ----------------

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            code TEXT,
            title TEXT,
            image_url TEXT,
            page_url TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_product(code, title, image, url):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO products VALUES (?, ?, ?, ?)",
        (code, title, image, url)
    )
    conn.commit()
    conn.close()

def get_product(code):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM products WHERE code=?", (code,))
    row = c.fetchone()
    conn.close()
    return row

# ---------------- SCRAPER ----------------

def search_lamelif(code):
    url = f"https://html.duckduckgo.com/html/?q={quote_plus('site:lamelif.com ' + code)}"
    r = requests.get(url, headers=HEADERS)

    soup = BeautifulSoup(r.text, "html.parser")

    for a in soup.select("a.result__a"):
        link = a.get("href")
        if "lamelif.com" in link:
            return link
    return None

def get_image_from_page(url):
    try:
        r = requests.get(url, headers=HEADERS)
        soup = BeautifulSoup(r.text, "html.parser")

        img = soup.find("img")
        if img and img.get("src"):
            return img.get("src")
    except:
        pass
    return None

# ---------------- API ----------------

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/search")
def search():
    code = request.args.get("code", "").upper().strip()

    if not code:
        return jsonify({"error": "kod boş"})

    # 🔥 1. ÖNCE DB
    cached = get_product(code)
    if cached:
        return jsonify({
            "source": "database",
            "code": code,
            "title": cached[1],
            "image": cached[2],
            "url": cached[3]
        })

    # 🔍 2. İNTERNETTEN BUL
    page = search_lamelif(code)

    if page:
        image = get_image_from_page(page)

        if image:
            save_product(code, page, image, page)

            return jsonify({
                "source": "internet",
                "code": code,
                "title": page,
                "image": image,
                "url": page
            })

    return jsonify({
        "code": code,
        "error": "bulunamadı"
    })

# ---------------- RUN ----------------

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

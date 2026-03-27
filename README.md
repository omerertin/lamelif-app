# Lamelif Ürün Görsel Bulucu

Bu uygulama ürün kodunu girince önce Lamelif ürün sayfasını bulmaya çalışır, sonra ürün sayfasındaki görseli çıkarır.

## Ne işe yarar?
- Depoda telefonla ürün kodu girersin.
- Sana direkt ürün görseli çıkar.
- Masadaki ürünle ekrandaki görseli karşılaştırırsın.

## Kurulum

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Tarayıcıdan aç:

```text
http://127.0.0.1:5000
```

## Telefonda kullanım
- En rahatı bunu Render, Railway, Fly.io veya kendi sunucuna koymak.
- Açtıktan sonra tarayıcıdan ana ekrana ekleyebilirsin.

## Google Görseller desteği
Daha kararlı Google görsel araması için şu iki ortam değişkenini ekleyebilirsin:

- `GOOGLE_API_KEY`
- `GOOGLE_CSE_ID`

Bu ikisi eklenirse uygulama önce Google Custom Search ile Lamelif sonuçlarını arar.

## Not
Google Görseller'i doğrudan kazımak kırılgan olduğu için uygulamada varsayılan güvenli yol şu:
1. Lamelif ürün sayfasını bul
2. Sayfadaki ürün görselini çek
3. Gerekirse Google Custom Search API ile destekle

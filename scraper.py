import json
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/122 Safari/537.36"
    )
}


def clean_price(value):
    cleaned = re.sub(r"[^\d]", "", value or "")
    return int(cleaned) if cleaned else None


def scrape_product(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)

        if response.status_code != 200:
            print("Bad status:", response.status_code)
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # BRAND + NAME (FŐ FIX)

        wrapper = soup.select_one("span.pjrv267")

        brand = ""
        name = ""

        if wrapper:
            brand_el = wrapper.find("a")
            name_el = wrapper.find("span")

            if brand_el:
                brand = brand_el.get_text(strip=True)

            if name_el:
                name = name_el.get_text(strip=True)

        full_name = f"{brand} {name}".strip()

        # CONCENTRATION

        conc_el = soup.select_one(".d1vwrfio")
        concentration = conc_el.get_text(strip=True) if conc_el else ""

        # SIZE

        all_text = soup.get_text(" ", strip=True)
        size_match = re.search(r"(\d+\s?(ml|g))", all_text, re.IGNORECASE)
        size = size_match.group(1) if size_match else ""

        # PRICES

        prices = []

        selectors = [
            'div.a5bcqfd [data-testid="pd-price-wrapper"] span[content]',
            'div.a1fmtqdl [data-testid="pd-price-wrapper"] span[content]',
            '#pd-price span[data-testid="pd-price"][content]',
        ]

        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                price = clean_price(el.get("content"))
                if price:
                    prices.append(price)

        price = min(prices) if prices else None

        # STOCK

        out_of_stock = soup.select_one(".a16l3874")
        in_stock = True

        if out_of_stock:
            text = out_of_stock.get_text(strip=True).lower()
            if "nincs raktáron" in text:
                in_stock = False

        # IMAGE

        image_el = soup.select_one('meta[property="og:image"]')
        image_url = image_el.get("content") if image_el else ""

        variants = []

        unique = {}

        for v in variants:
            key = v["url"]  # ez a legstabilabb azonosító

            if key in unique:
                continue

            unique[key] = v

        variants = list(unique.values())

        scripts = soup.find_all("script", attrs={"type": "application/ld+json"})

        product_json = None

        for s in scripts:
            try:
                data = json.loads(s.string)

                # több JSON esetén
                if isinstance(data, list):
                    for item in data:
                        if item.get("@type") == "Product":
                            product_json = item
                else:
                    if data.get("@type") == "Product":
                        product_json = data

            except Exception:
                continue

        offers = product_json.get("offers", [])

        if isinstance(offers, dict):
            offers = [offers]

        for offer in offers:
            size_match = re.search(r"(\d+)\s*ml", offer.get("name", ""), re.I)

            variants.append(
                {
                    "size": size_match.group(1) + " ml" if size_match else "",
                    "price": int(offer["price"]) if offer.get("price") else None,
                    "image_url": offer.get("image", image_url),
                    "url": (
                        "https://www.notino.hu" + offer["url"]
                        if offer.get("url")
                        else url
                    ),
                    "in_stock": ("InStock" in offer.get("availability", "")),
                }
            )
        else:
            print("NO PRODUCT JSON FOUND")

        return {
            "name": full_name,
            "brand": brand,
            "concentration": concentration,
            "variants": variants,
            "in_stock": in_stock,
            "size": size,
        }

    except Exception as e:
        print("Scraping error:", e)
        return None

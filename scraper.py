import json
import random
import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def clean_price(value):
    cleaned = re.sub(r"[^\d]", "", value or "")
    return int(cleaned) if cleaned else None


def is_allowed_notino_url(url):
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in {"notino.hu", "www.notino.hu"}
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except (TypeError, ValueError):
        return False


def scrape_product(url, max_retries=3):
    try:
        if not is_allowed_notino_url(url):
            print("Rejected non-Notino URL")
            return None

        response = None
        with cffi_requests.Session(impersonate="chrome124") as session:
            session.headers.update(HEADERS)

            for attempt in range(max_retries + 1):
                current_url = url
                response = None

                for _ in range(6):
                    response = session.get(
                        current_url, timeout=15, allow_redirects=False
                    )
                    if response.status_code not in (301, 302, 303, 307, 308):
                        break
                    location = response.headers.get("Location")
                    if not location:
                        return None
                    current_url = urljoin(current_url, location)
                    if not is_allowed_notino_url(current_url):
                        print("Rejected redirect outside Notino")
                        return None
                else:
                    print("Too many Notino redirects")
                    return None

                if response.status_code == 200:
                    break

                if response.status_code in (403, 429) and attempt < max_retries:
                    sleep_sec = random.uniform(2.5, 5.0) * (attempt + 1)
                    print(
                        f"Status {response.status_code} on {current_url}, "
                        f"retrying in {sleep_sec:.1f}s..."
                    )
                    time.sleep(sleep_sec)
                    continue

                print(f"Bad status: {response.status_code} -> {current_url}")
                return None

        if response is None or response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # BRAND + NAME
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

        # VARIANTS (JSON-LD)
        variants = []
        scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
        offers = []

        for s in scripts:
            try:
                if not s.string:
                    continue
                data = json.loads(s.string)

                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            item_offers = item.get("offers", [])
                            if isinstance(item_offers, dict):
                                item_offers = [item_offers]
                            offers.extend(item_offers)
                elif isinstance(data, dict) and data.get("@type") == "Product":
                    item_offers = data.get("offers", [])
                    if isinstance(item_offers, dict):
                        item_offers = [item_offers]
                    offers.extend(item_offers)

            except Exception:
                continue

        unique = {}
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            key = offer.get("url") or offer.get("name")
            if key in unique:
                continue
            unique[key] = offer

        for offer in unique.values():
            v_size_match = re.search(r"(\d+)\s*ml", offer.get("name", ""), re.I)

            variants.append(
                {
                    "size": v_size_match.group(1) + " ml" if v_size_match else "",
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
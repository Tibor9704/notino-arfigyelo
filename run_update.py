from datetime import datetime
import requests
import time

from app import app, normalize_size
from models import Product, PriceHistory, db
from scraper import scrape_product

LOG_FILE = "update_log.txt"


def log(message):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")


def wait_for_internet():
    while True:
        try:
            requests.get("https://www.google.com", timeout=5)
            log(f"[{datetime.now()}] INTERNET OK")
            return
        except Exception:
            log(f"[{datetime.now()}] NO INTERNET - retry in 30 sec")
            time.sleep(30)


def get_latest_price(product_id):
    latest = (
        PriceHistory.query.filter_by(product_id=product_id)
        .order_by(PriceHistory.checked_at.desc())
        .first()
    )
    return latest.price if latest else None


def run_update():
    wait_for_internet()

    with app.app_context():
        products = Product.query.all()

        log(f"[{datetime.now()}] START UPDATE - {len(products)} products")

        for product in products:
            try:
                data = scrape_product(product.url)

                if not data:
                    log(f"[{datetime.now()}] SCRAPE FAILED -> {product.name}")
                    continue

                variants = data.get("variants") or []

                # variant match
                matched = None
                for v in variants:
                    if normalize_size(v["size"]) == normalize_size(product.size):
                        matched = v
                        break

                if not matched:
                    log(f"[{datetime.now()}] NO VARIANT MATCH -> {product.name}")
                    continue

                price = matched["price"]
                stock = matched["in_stock"]

                # stock update + restock detection
                if product.in_stock is False and stock is True:
                    product.restocked_at = datetime.utcnow()
                    log(f"[{datetime.now()}] RESTOCKED -> {product.name}")

                product.in_stock = stock

                # PRICE LOGIC
                if price is None:
                    log(f"[{datetime.now()}] NO PRICE -> {product.name}")
                    continue

                last_price = get_latest_price(product.id)

                if last_price == price:
                    log(f"[{datetime.now()}] NO CHANGE -> {product.name} ({price})")

                    latest = (
                        PriceHistory.query.filter_by(product_id=product.id)
                        .order_by(PriceHistory.checked_at.desc())
                        .first()
                    )

                    if latest:
                        latest.checked_at = datetime.utcnow()

                    continue

                # új ár mentése
                history = PriceHistory(
                    product_id=product.id, price=price, checked_at=datetime.utcnow()
                )

                db.session.add(history)

                log(f"[{datetime.now()}] PRICE UPDATE -> {product.name} -> {price} Ft")

                time.sleep(0.4)

            except Exception as e:
                log(f"[{datetime.now()}] ERROR -> {product.name} -> {e}")
                continue

        db.session.commit()

        log(f"[{datetime.now()}] UPDATE FINISHED")


if __name__ == "__main__":
    run_update()

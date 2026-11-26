from datetime import datetime, timezone
import random
import time

from app import app, check_price_alert, normalize_size
from models import PriceHistory, Product, db
from scraper import scrape_product

LOG_FILE = "update_log.txt"


def log(message):
    print(message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")


def get_latest_price(product_id):
    latest = (
        PriceHistory.query.filter_by(product_id=product_id)
        .order_by(PriceHistory.checked_at.desc())
        .first()
    )
    return latest.price if latest else None


def scrape_with_fallback_retry(url, retries=1):
    for attempt in range(retries + 1):
        data = scrape_product(url)
        if data is not None:
            return data
        if attempt < retries:
            time.sleep(random.uniform(3.0, 6.0))
    return None


def pause_between_items():
    time.sleep(random.uniform(1.5, 3.0))


def run_update():
    with app.app_context():
        products = Product.query.all()
        now_utc = lambda: datetime.now(timezone.utc)

        log(f"[{now_utc()}] START UPDATE - {len(products)} products")

        for item in products:
            try:
                data = scrape_with_fallback_retry(item.url, retries=1)

                if not data:
                    log(f"[{now_utc()}] SCRAPE FAILED -> {item.name}")
                    pause_between_items()
                    continue

                variants = data.get("variants") or []

                if not variants:
                    log(f"[{now_utc()}] NO VARIANTS FOUND -> {item.name}")
                    pause_between_items()
                    continue

                if item.size == "Mérés alatt":
                    log(f"[{now_utc()}] PROCESSING NEW PRODUCT URL: {item.url}")

                    first_v = variants[0]
                    item.name = data["name"]
                    item.brand = data["brand"]
                    item.concentration = data["concentration"]
                    item.size = first_v["size"]
                    item.image_url = first_v["image_url"]
                    item.url = first_v["url"].rstrip("/") + "/"
                    item.in_stock = first_v["in_stock"]

                    db.session.flush()

                    if first_v["price"] is not None:
                        db.session.add(
                            PriceHistory(
                                product_id=item.id,
                                price=first_v["price"],
                                checked_at=now_utc(),
                            )
                        )

                    log(
                        f"[{now_utc()}] FIRST VARIANT ADDED -> "
                        f"{item.name} ({item.size})"
                    )

                    for extra_v in variants[1:]:
                        extra_url = extra_v["url"].rstrip("/") + "/"

                        exists = Product.query.filter_by(
                            user_id=item.user_id, url=extra_url
                        ).first()
                        if exists:
                            continue

                        new_product = Product(
                            user_id=item.user_id,
                            name=data["name"],
                            brand=data["brand"],
                            concentration=data["concentration"],
                            size=extra_v["size"],
                            image_url=extra_v["image_url"],
                            url=extra_url,
                            in_stock=extra_v["in_stock"],
                        )
                        db.session.add(new_product)
                        db.session.flush()

                        if extra_v["price"] is not None:
                            db.session.add(
                                PriceHistory(
                                    product_id=new_product.id,
                                    price=extra_v["price"],
                                    checked_at=now_utc(),
                                )
                            )
                        log(
                            f"[{now_utc()}] EXTRA VARIANT GENERATED -> "
                            f"{data['name']} ({extra_v['size']})"
                        )

                    db.session.commit()
                    pause_between_items()
                    continue

                matched = None
                for v in variants:
                    if normalize_size(v["size"]) == normalize_size(item.size):
                        matched = v
                        break

                if not matched:
                    log(
                        f"[{now_utc()}] NO VARIANT MATCH -> "
                        f"{item.name} ({item.size})"
                    )
                    pause_between_items()
                    continue

                price = matched["price"]
                stock = matched["in_stock"]

                if item.in_stock is False and stock is True:
                    item.restocked_at = now_utc()
                    log(f"[{now_utc()}] RESTOCKED -> {item.name}")

                item.in_stock = stock

                if price is None:
                    log(f"[{now_utc()}] NO PRICE -> {item.name}")
                    pause_between_items()
                    continue

                check_price_alert(item, price)

                last_price = get_latest_price(item.id)

                if last_price == price:
                    log(f"[{now_utc()}] NO CHANGE -> {item.name} ({price})")
                    latest = (
                        PriceHistory.query.filter_by(product_id=item.id)
                        .order_by(PriceHistory.checked_at.desc())
                        .first()
                    )
                    if latest:
                        latest.checked_at = now_utc()
                    db.session.commit()
                    pause_between_items()
                    continue

                history = PriceHistory(
                    product_id=item.id,
                    price=price,
                    checked_at=now_utc(),
                )
                db.session.add(history)
                db.session.commit()

                log(
                    f"[{now_utc()}] PRICE UPDATE -> "
                    f"{item.name} ({item.size}) -> {price} Ft"
                )
                pause_between_items()

            except Exception as e:
                db.session.rollback()
                log(f"[{now_utc()}] ERROR -> {item.name} -> {e}")
                pause_between_items()
                continue

        db.session.commit()
        log(f"[{now_utc()}] UPDATE FINISHED")


if __name__ == "__main__":
    run_update()
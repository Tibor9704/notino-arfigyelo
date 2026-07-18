from datetime import datetime
import os
import time
from app import app, normalize_size
from models import Product, PriceHistory, db
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


def run_update():
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

                if not variants:
                    log(f"[{datetime.now()}] NO VARIANTS FOUND -> {product.name}")
                    continue

                if product.size == "Mérés alatt":
                    log(f"[{datetime.now()}] PROCESSING NEW PRODUCT URL: {product.url}")
                    
                    first_v = variants[0]
                    product.name = data["name"]
                    product.brand = data["brand"]
                    product.concentration = data["concentration"]
                    product.size = first_v["size"]
                    product.image_url = first_v["image_url"]
                    product.url = first_v["url"].rstrip("/") + "/"
                    product.in_stock = first_v["in_stock"]
                    
                    db.session.flush() 
                    
                    if first_v["price"] is not None:
                        db.session.add(PriceHistory(
                            product_id=product.id,
                            price=first_v["price"],
                            checked_at=datetime.utcnow()
                        ))
                    
                    log(f"[{datetime.now()}] FIRST VARIANT ADDED -> {product.name} ({product.size})")

                    for extra_v in variants[1:]:
                        extra_url = extra_v["url"].rstrip("/") + "/"
                        
                        exists = Product.query.filter_by(user_id=product.user_id, url=extra_url).first()
                        if exists:
                            continue

                        new_product = Product(
                            user_id=product.user_id,
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
                            db.session.add(PriceHistory(
                                product_id=new_product.id,
                                price=extra_v["price"],
                                checked_at=datetime.utcnow()
                            ))
                        log(f"[{datetime.now()}] EXTRA VARIANT GENERATED -> {data['name']} ({extra_v['size']})")
                    
                    db.session.commit()
                    time.sleep(1)
                    continue

                matched = None
                for v in variants:
                    if normalize_size(v["size"]) == normalize_size(product.size):
                        matched = v
                        break

                if not matched:
                    log(f"[{datetime.now()}] NO VARIANT MATCH -> {product.name} ({product.size})")
                    continue

                price = matched["price"]
                stock = matched["in_stock"]

                if product.in_stock is False and stock is True:
                    product.restocked_at = datetime.utcnow()
                    log(f"[{datetime.now()}] RESTOCKED -> {product.name}")

                product.in_stock = stock

                if price is None:
                    log(f"[{datetime.now()}] NO PRICE -> {product.name}")
                    continue

                last_price = get_latest_price(product.id)

                if last_price == price:
                    log(f"[{datetime.now()}] NO CHANGE -> {product.name} ({price})")
                    latest = PriceHistory.query.filter_by(product_id=product.id).order_by(PriceHistory.checked_at.desc()).first()
                    if latest:
                        latest.checked_at = datetime.utcnow()
                    continue

                history = PriceHistory(
                    product_id=product.id,
                    price=price,
                    checked_at=datetime.utcnow(),
                )
                db.session.add(history)

                log(f"[{datetime.now()}] PRICE UPDATE -> {product.name} ({product.size}) -> {price} Ft")
                time.sleep(1)

            except Exception as e:
                log(f"[{datetime.now()}] ERROR -> {product.name} -> {e}")
                continue

        db.session.commit()
        log(f"[{datetime.now()}] UPDATE FINISHED")

if __name__ == "__main__":
    run_update()
from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime
from datetime import timedelta
import re
from config import Config
from models import db, Product, PriceHistory
from scraper import scrape_product

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

with app.app_context():
    db.create_all()


# HELPERS


def extract_ml(size):
    if not size:
        return None

    match = re.search(r"(\d+)\s*ml", size.lower())
    return int(match.group(1)) if match else None


def price_per_ml(price, size):
    ml = extract_ml(size)
    if not ml or not price:
        return None
    return int(float(price)) // ml


def normalize_size(s):
    return (s or "").replace(" ", "").lower()


def normalize_concentration(s):
    value = (s or "").strip().lower()
    ignored_words = ("utántölthető",)

    for word in ignored_words:
        value = value.replace(word, "")

    return " ".join(value.split())


def normalize_product_name(product):
    name = (product.name or "").strip().lower()

    return " ".join(name.split())


def normalize_url(url):
    return url.rstrip("/") + "/"


def product_group_key(product):
    return (
        normalize_product_name(product),
        normalize_concentration(product.concentration),
    )


def size_sort_key(product):
    digits = "".join(ch for ch in (product.size or "") if ch.isdigit())
    return (int(digits) if digits else 999999, product.size or "")


def get_group_products(product):
    key = product_group_key(product)
    products = [
        candidate
        for candidate in Product.query.all()
        if product_group_key(candidate) == key
    ]
    return sorted(products, key=size_sort_key)


def build_chart_data(products):
    histories_by_product = {}
    all_prices = []
    all_labels = set()

    for product in products:
        history = (
            PriceHistory.query.filter_by(product_id=product.id)
            .order_by(PriceHistory.checked_at.asc())
            .all()
        )
        clean_history = [h for h in history if h.price is not None]
        histories_by_product[product.id] = clean_history

        for point in clean_history:
            all_labels.add(point.checked_at.strftime("%Y-%m-%d %H:%M"))
            all_prices.append(point.price)

    labels = sorted(all_labels)

    datasets = []
    for product in products:
        prices_by_time = {
            point.checked_at.strftime("%Y-%m-%d %H:%M"): point.price
            for point in histories_by_product[product.id]
        }
        datasets.append(
            {
                "label": product.size or "Méret nélkül",
                "data": [prices_by_time.get(label) for label in labels],
            }
        )

    return {
        "labels": labels,
        "datasets": datasets,
        "prices": all_prices,
    }


# PRICE UPDATE LOGIC


def update_product_price(product):
    latest = (
        PriceHistory.query.filter_by(product_id=product.id)
        .order_by(PriceHistory.checked_at.desc())
        .first()
    )

    # napi limit
    if latest and latest.checked_at.date() == datetime.utcnow().date():
        print(f"Already checked today: {product.name}")
        return

    data = scrape_product(product.url)
    if not data:
        return

    variants = data.get("variants") or []

    matched = None

    for v in variants:
        if normalize_size(v["size"]) == normalize_size(product.size):
            matched = v
            break

    if not matched:
        print(f"No matching variant: {product.name} ({product.size})")
        return

    price = matched["price"]
    stock = matched["in_stock"]

    # restock detect
    if product.in_stock is False and stock is True:
        product.restocked_at = datetime.utcnow()
        print(f"{product.name} újra készleten!")

    product.in_stock = stock

    # nincs ár
    if price is None:
        db.session.commit()
        print(f"{product.name} nincs ár")
        return

    # nincs változás
    if latest and latest.price == price:
        latest.checked_at = datetime.utcnow()
        db.session.commit()
        print(f"No price change: {product.name}")
        return

    db.session.add(
        PriceHistory(
            product_id=product.id,
            price=price,
            checked_at=datetime.utcnow(),
        )
    )

    db.session.commit()


def update_all_prices():
    with app.app_context():
        for product in Product.query.all():
            update_product_price(product)


# scheduler = BackgroundScheduler()
# scheduler.add_job(update_all_prices, trigger="interval", hours=1)
# scheduler.start()


# INDEX


@app.route("/")
def index():
    search = request.args.get("search", "").strip()

    query = Product.query
    if search:
        query = query.filter(Product.name.ilike(f"%{search}%"))

    products = query.all()

    latest_prices = {}
    lowest_prices = {}
    enriched = []

    for product in products:
        history = (
            PriceHistory.query.filter_by(product_id=product.id)
            .order_by(PriceHistory.checked_at.desc())
            .all()
        )

        latest = history[0] if history else None
        previous = None

        if latest:
            for h in history[1:]:
                if h.price != latest.price:
                    previous = h
                    break

        latest_prices[product.id] = {
            "latest": latest,
            "previous": previous,
            "history_count": len(history),
        }

        prices = [h.price for h in history if h.price is not None]
        lowest_prices[product.id] = min(prices) if prices else None

        percent = 0
        change_amount = 0
        is_best_deal = False
        show_badge = False

        if latest and previous and previous.price:
            change_amount = latest.price - previous.price
            percent = round((change_amount / previous.price) * 100, 1)

            show_badge = abs(percent) >= 1

        if latest and len(history) > 1:
            previous_prices = [h.price for h in history[1:] if h.price is not None]

            if previous_prices:
                historical_low = min(previous_prices)
                is_best_deal = latest.price <= historical_low * 0.9

        enriched.append(
            {
                "product": product,
                "percent": percent,
                "is_best_deal": is_best_deal,
                "show_badge": show_badge,
                "restocked_at": product.restocked_at,
                "change_amount": change_amount,
            }
        )

    enriched.sort(
        key=lambda x: (not x["is_best_deal"], not x["restocked_at"], x["change_amount"])
    )

    groups = {}

    for item in enriched:
        product = item["product"]
        key = product_group_key(product)

        if key not in groups:
            groups[key] = {
                "product": product,
                "variants": [],
                "is_best_deal": False,
                "show_badge": False,
                "percent": 0,
                "restocked_at": None,
                "change_amount": 0,
                "in_stock": False,
            }

        group = groups[key]
        group["variants"].append(item)
        group["is_best_deal"] = group["is_best_deal"] or item["is_best_deal"]
        group["in_stock"] = group["in_stock"] or bool(product.in_stock)

        if item["restocked_at"] and (
            group["restocked_at"] is None
            or item["restocked_at"] > group["restocked_at"]
        ):
            group["restocked_at"] = item["restocked_at"]

        if item["show_badge"] and (
            not group["show_badge"] or abs(item["percent"]) > abs(group["percent"])
        ):
            group["show_badge"] = True
            group["percent"] = item["percent"]

        if item["change_amount"] < group["change_amount"]:
            group["change_amount"] = item["change_amount"]

    grouped_enriched = list(groups.values())

    for group in grouped_enriched:
        group["variants"].sort(key=lambda x: size_sort_key(x["product"]))

    grouped_enriched.sort(
        key=lambda x: (not x["is_best_deal"], not x["restocked_at"], x["change_amount"])
    )

    return render_template(
        "index.html",
        grouped_enriched=grouped_enriched,
        latest_prices=latest_prices,
        lowest_prices=lowest_prices,
        search=search,
    )


# ADD


@app.route("/add", methods=["POST"])
def add_product():
    url = request.form.get("url")
    if not url:
        return redirect(url_for("index"))

    clean_url = url.strip().rstrip("/") + "/"

    exists = Product.query.filter_by(url=clean_url).first()

    if not exists:
        product = Product(
            name="Új termék (Feldolgozás alatt...)",
            brand=None,
            concentration=None,
            size="Mérés alatt",
            image_url="https://via.placeholder.com/150", 
            url=clean_url,
            in_stock=True,
            created_at=datetime.utcnow()
        )
        
        try:
            db.session.add(product)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Hiba a termék mentése során: {e}")

    return redirect(url_for("index"))

# DELETE


@app.route("/delete/<int:product_id>", methods=["POST"])
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)

    PriceHistory.query.filter_by(product_id=product.id).delete()

    db.session.delete(product)
    db.session.commit()

    return redirect(url_for("index"))


@app.route("/delete-group/<int:product_id>", methods=["POST"])
def delete_product_group(product_id):
    product = Product.query.get_or_404(product_id)
    variants = get_group_products(product)

    for variant in variants:
        PriceHistory.query.filter_by(product_id=variant.id).delete()
        db.session.delete(variant)

    db.session.commit()

    return redirect(url_for("index"))


# DETAIL


@app.route("/product-group/<int:product_id>")
def product_group_detail(product_id):
    product = Product.query.get_or_404(product_id)
    variants = get_group_products(product)
    chart_data = build_chart_data(variants)

    ml_prices = []

    for v in variants:
        latest = (
            PriceHistory.query.filter_by(product_id=v.id)
            .order_by(PriceHistory.checked_at.desc())
            .first()
        )

        ml = extract_ml(v.size)
        ppm = int(latest.price) // ml if ml and latest and latest.price else None

        ml_prices.append((v.id, ppm))

    valid = [x for x in ml_prices if x[1] is not None]

    best_variant_id = None
    if valid:
        best_variant_id = min(valid, key=lambda x: x[1])[0]

    return render_template(
        "product.html",
        product=product,
        variants=variants,
        chart_data=chart_data,
        active_product_id=None,
        is_group=True,
        best_variant_id=best_variant_id,
    )


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    variants = get_group_products(product)
    chart_data = build_chart_data([product])
    prices = chart_data["prices"]

    lowest_price = min(prices) if prices else None
    highest_price = max(prices) if prices else None
    latest_price = prices[-1] if prices else None

    ml = extract_ml(product.size)
    price_per_ml = int(float(latest_price)) // ml if ml and latest_price else None

    ml_prices = []

    for v in variants:
        latest = (
            PriceHistory.query.filter_by(product_id=v.id)
            .order_by(PriceHistory.checked_at.desc())
            .first()
        )

        ml = extract_ml(v.size)
        ppm = int(latest.price) // ml if ml and latest and latest.price else None

        ml_prices.append((v.id, ppm))

    valid = [x for x in ml_prices if x[1] is not None]

    best_variant_id = None
    if valid:
        best_variant_id = min(valid, key=lambda x: x[1])[0]

    is_best_price = (
        latest_price is not None
        and lowest_price is not None
        and highest_price is not None
        and latest_price == lowest_price
        and lowest_price != highest_price
    )

    return render_template(
        "product.html",
        product=product,
        variants=variants,
        chart_data=chart_data,
        lowest_price=lowest_price,
        highest_price=highest_price,
        latest_price=latest_price,
        price_per_ml=price_per_ml,
        is_best_price=is_best_price,
        active_product_id=product.id,
        best_variant_id=best_variant_id,
        is_group=False,
    )


@app.route("/stats")
def stats():
    periods = {
        "Hónap": 30,
        "Negyedév": 90,
        "Fél év": 180,
        "Év": 365,
    }

    stats = {}
    now = datetime.utcnow()

    for label, days in periods.items():
        cutoff = now - timedelta(days=days)

        changes = []
        activity = []

        for product in Product.query.all():
            history = (
                PriceHistory.query.filter_by(product_id=product.id)
                .filter(PriceHistory.checked_at >= cutoff)
                .order_by(PriceHistory.checked_at.asc())
                .all()
            )

            if len(history) < 2:
                continue

            # ----- ármódosítások száma -----

            change_count = 0

            for i in range(1, len(history)):
                if history[i].price != history[i - 1].price:
                    change_count += 1

            activity.append(
                {
                    "name": product.name,
                    "size": product.size,
                    "count": change_count,
                }
            )

            # ----- drágulás / áresés -----

            first_price = history[0].price
            latest_price = history[-1].price

            if first_price is None or latest_price is None:
                continue

            diff = latest_price - first_price

            changes.append(
                {
                    "name": product.name,
                    "size": product.size,
                    "change": diff,
                }
            )

        most_active = sorted(
            activity,
            key=lambda x: x["count"],
            reverse=True,
        )[:5]

        biggest_up = sorted(
            [c for c in changes if c["change"] > 0],
            key=lambda x: x["change"],
            reverse=True,
        )[:5]

        biggest_down = sorted(
            [c for c in changes if c["change"] < 0],
            key=lambda x: x["change"],
        )[:5]

        stats[label] = {
            "top5": most_active,
            "up": biggest_up,
            "down": biggest_down,
        }

    return render_template(
        "stats.html",
        stats=stats,
    )


# RUN


if __name__ == "__main__":
    app.run(debug=True)

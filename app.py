from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from datetime import datetime
from datetime import timedelta
from email.message import EmailMessage
import smtplib
import os
from bisect import bisect_left
import re
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash
import hmac
import secrets
import click
from config import Config
from models import db, Product, PriceHistory, PriceAlert, User
from scraper import is_allowed_notino_url, scrape_product

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY
LEGACY_USERNAME = "legacy"


def get_csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

db.init_app(app)


@app.context_processor
def inject_current_user():
    return {"current_user": get_current_user(), "csrf_token": get_csrf_token()}


def ensure_products_user_column():
    with app.app_context():
        inspector = inspect(db.engine)
        if not inspector.has_table("products"):
            return

        columns = [column["name"] for column in inspector.get_columns("products")]
        if "user_id" not in columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE products ADD COLUMN user_id INTEGER"))



def get_or_create_legacy_user():
    legacy_user = User.query.filter_by(username=LEGACY_USERNAME).first()
    if legacy_user is None:
        legacy_user = User(
            username=LEGACY_USERNAME,
            password_hash=generate_password_hash(secrets.token_urlsafe(32)),
        )
        db.session.add(legacy_user)
        db.session.commit()

    return legacy_user



def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = User.query.get(user_id)
    if user and user.username == LEGACY_USERNAME:
        return None
    return user


@app.before_request
def require_authentication():
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        expected_token = session.get("csrf_token", "")
        if not expected_token or not hmac.compare_digest(submitted_token, expected_token):
            abort(400, description="Invalid or missing CSRF token")

    allowed_routes = {"login", "register", "static"}
    if request.endpoint in allowed_routes:
        return None

    if not get_current_user():
        return redirect(url_for("login"))


with app.app_context():
    db.create_all()
    history_index = next(
        index for index in PriceHistory.__table__.indexes
        if index.name == "ix_price_history_product_checked"
    )
    history_index.create(bind=db.engine, checkfirst=True)
    ensure_products_user_column()
    legacy_user = get_or_create_legacy_user()
    Product.query.filter(Product.user_id.is_(None)).update({"user_id": legacy_user.id})
    db.session.commit()


@app.cli.command("claim-legacy-products")
@click.option("--username", required=True, help="The registered account to receive the legacy products.")
def claim_legacy_products(username):
    """Move legacy watchlist products to a registered account."""
    target_user = User.query.filter_by(username=username.strip()).first()
    if target_user is None:
        raise click.ClickException(f"No account found for username '{username}'. Register first.")
    if target_user.username == LEGACY_USERNAME:
        raise click.ClickException("The legacy account cannot claim its own products.")

    legacy_user = User.query.filter_by(username=LEGACY_USERNAME).first()
    if legacy_user is None:
        raise click.ClickException("The legacy account does not exist.")

    legacy_products = Product.query.filter_by(user_id=legacy_user.id).all()
    if not legacy_products:
        click.echo("There are no legacy products to transfer.")
        return

    target_urls = {
        url
        for (url,) in db.session.query(Product.url).filter_by(user_id=target_user.id).all()
    }
    duplicate_urls = [
        product.url for product in legacy_products if product.url in target_urls
    ]
    if duplicate_urls:
        raise click.ClickException(
            "Transfer stopped: the target account already tracks one or more legacy products. "
            "Remove duplicate URLs from one account before retrying."
        )

    for product in legacy_products:
        product.user_id = target_user.id
    db.session.commit()
    click.echo(
        f"Transferred {len(legacy_products)} legacy product(s) to '{target_user.username}'."
    )


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


def get_user_brand_names(user_id):
    brand_map = {}
    for product in Product.query.filter_by(user_id=user_id).all():
        brand = (product.brand or "").strip()
        if brand:
            brand_map.setdefault(brand.casefold(), brand)
    return sorted(brand_map.values(), key=lambda brand: (-len(brand), brand.casefold()))


def infer_brand_from_name(name, brand_names):
    normalized_name = (name or "").strip().casefold()
    for brand in brand_names:
        normalized_brand = brand.strip().casefold()
        if normalized_name.startswith(normalized_brand) and (
            len(normalized_name) == len(normalized_brand)
            or not normalized_name[len(normalized_brand)].isalnum()
        ):
            return brand
    return None

def get_group_products(product):
    key = product_group_key(product)
    products = [
        candidate
        for candidate in Product.query.filter_by(user_id=product.user_id).all()
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


def email_alert_is_configured():
    return bool(
        os.environ.get("SMTP_HOST")
        and (os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME"))
    )


def send_price_alert_email(alert, product, price):
    host = os.environ.get("SMTP_HOST")
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME")
    if not host or not sender:
        app.logger.error("Price alert email is not configured; set SMTP_HOST and SMTP_FROM")
        return False

    message = EmailMessage()
    message["Subject"] = f"Elérte a célárat: {product.name} ({product.size or 'kiszerelés'})"
    message["From"] = sender
    message["To"] = alert.email
    message.set_content(
        f"A(z) {product.name} ({product.size or 'kiszerelés'}) ára elérte a megadott célárat.\n\n"
        f"Aktuális ár: {price} Ft\n"
        f"Célár: {alert.target_price} Ft\n"
        f"Termék: {product.url}\n"
    )

    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() not in {"0", "false", "no"}
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
            username = os.environ.get("SMTP_USERNAME")
            password = os.environ.get("SMTP_PASSWORD")
            if username:
                server.login(username, password or "")
            server.send_message(message)
        return True
    except Exception:
        app.logger.exception("Could not send price alert for product %s", product.id)
        return False


def check_price_alert(product, price):
    alert = PriceAlert.query.filter_by(product_id=product.id).first()
    if not alert:
        return

    if price > alert.target_price:
        alert.notified_at = None
        alert.notified_price = None
        return

    if alert.notified_at:
        return

    if send_price_alert_email(alert, product, price):
        alert.notified_at = datetime.utcnow()
        alert.notified_price = price

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
    check_price_alert(product, price)

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
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()

    query = Product.query.filter_by(user_id=current_user.id)
    if search:
        query = query.filter(Product.name.ilike(f"%{search}%"))

    products = query.all()
    brand_match_names = get_user_brand_names(current_user.id)
    brands = sorted(brand_match_names, key=str.casefold)
    brand_filter = request.args.get("brand", "").strip()
    effective_brands = {
        product.id: (product.brand or "").strip() or infer_brand_from_name(product.name, brand_match_names)
        for product in products
    }
    if brand_filter:
        products = [
            product for product in products
            if (effective_brands[product.id] or "").casefold() == brand_filter.casefold()
        ]

    history_by_product = {}
    product_ids = [product.id for product in products]
    if product_ids:
        history_rows = (
            PriceHistory.query.filter(PriceHistory.product_id.in_(product_ids))
            .order_by(
                PriceHistory.product_id.asc(),
                PriceHistory.checked_at.desc(),
                PriceHistory.id.desc(),
            )
            .all()
        )
        for history_row in history_rows:
            history_by_product.setdefault(history_row.product_id, []).append(history_row)

    latest_prices = {}
    lowest_prices = {}
    enriched = []

    for product in products:
        history = history_by_product.get(product.id, [])

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

    total_groups = len(grouped_enriched)
    per_page = 12
    total_pages = max(1, (total_groups + per_page - 1) // per_page)
    page = request.args.get("page", 1, type=int) or 1
    page = min(max(page, 1), total_pages)
    start = (page - 1) * per_page
    grouped_enriched = grouped_enriched[start:start + per_page]

    return render_template(
        "index.html",
        grouped_enriched=grouped_enriched,
        latest_prices=latest_prices,
        lowest_prices=lowest_prices,
        search=search,
        brands=brands,
        brand_filter=brand_filter,
        total_groups=total_groups,
        page=page,
        total_pages=total_pages,
    )


# ADD


@app.route("/add", methods=["POST"])
def add_product():
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    url = request.form.get("url")
    if not url:
        return redirect(url_for("index"))

    clean_url = url.strip()
    if not is_allowed_notino_url(clean_url):
        flash("Csak HTTPS-es Notino.hu termékoldal adható hozzá.")
        return redirect(url_for("index"))

    clean_url = clean_url.rstrip("/") + "/"

    exists = Product.query.filter_by(user_id=current_user.id, url=clean_url).first()

    if not exists:
        product = Product(
            user_id=current_user.id,
            name="Új termék (Feldolgozás alatt...)",
            brand=None,
            concentration=None,
            size="Mérés alatt",
            image_url="https://via.placeholder.com/150",
            url=clean_url,
            in_stock=True,
            created_at=datetime.utcnow(),
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
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    product = Product.query.filter_by(user_id=current_user.id, id=product_id).first_or_404()

    PriceAlert.query.filter_by(product_id=product.id).delete()
    PriceHistory.query.filter_by(product_id=product.id).delete()

    db.session.delete(product)
    db.session.commit()

    return redirect(url_for("index"))


@app.route("/delete-group/<int:product_id>", methods=["POST"])
def delete_product_group(product_id):
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    product = Product.query.filter_by(user_id=current_user.id, id=product_id).first_or_404()
    variants = get_group_products(product)

    for variant in variants:
        PriceAlert.query.filter_by(product_id=variant.id).delete()
        PriceHistory.query.filter_by(product_id=variant.id).delete()
        db.session.delete(variant)

    db.session.commit()

    return redirect(url_for("index"))


# DETAIL


@app.route("/product-group/<int:product_id>")
def product_group_detail(product_id):
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    product = Product.query.filter_by(user_id=current_user.id, id=product_id).first_or_404()
    variants = get_group_products(product)
    chart_data = build_chart_data(variants)
    brand_names = get_user_brand_names(current_user.id)
    product_brand = (product.brand or "").strip() or infer_brand_from_name(product.name, brand_names)

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
        product_brand=product_brand,
        variants=variants,
        chart_data=chart_data,
        active_product_id=None,
        is_group=True,
        best_variant_id=best_variant_id,
    )


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    product = Product.query.filter_by(user_id=current_user.id, id=product_id).first_or_404()
    variants = get_group_products(product)
    chart_data = build_chart_data([product])
    brand_names = get_user_brand_names(current_user.id)
    product_brand = (product.brand or "").strip() or infer_brand_from_name(product.name, brand_names)
    price_alert = PriceAlert.query.filter_by(product_id=product.id).first()
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
        product_brand=product_brand,
        price_alert=price_alert,
        email_alert_configured=email_alert_is_configured(),
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


@app.route("/product/<int:product_id>/price-alert", methods=["POST"])
def save_price_alert(product_id):
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    product = Product.query.filter_by(
        user_id=current_user.id, id=product_id
    ).first_or_404()
    alert = PriceAlert.query.filter_by(product_id=product.id).first()

    if request.form.get("action") == "delete":
        if alert:
            db.session.delete(alert)
            db.session.commit()
            flash("Az árértesítőt töröltük.")
        return redirect(url_for("product_detail", product_id=product.id))

    email = request.form.get("email", "").strip()
    target_text = request.form.get("target_price", "").strip()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        flash("Adj meg egy érvényes email címet.")
        return redirect(url_for("product_detail", product_id=product.id))

    try:
        target_price = int(target_text)
        if target_price <= 0:
            raise ValueError
    except ValueError:
        flash("A célár pozitív, egész forintösszeg legyen.")
        return redirect(url_for("product_detail", product_id=product.id))

    if alert is None:
        alert = PriceAlert(product_id=product.id)
        db.session.add(alert)

    alert.email = email
    alert.target_price = target_price
    alert.notified_at = None
    alert.notified_price = None
    db.session.commit()
    flash("Az árértesítőt elmentettük. A következő napi árfrissítéseknél ellenőrizzük.")
    return redirect(url_for("product_detail", product_id=product.id))

@app.route("/stats")
def stats():
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    periods = {
        "Hónap": 30,
        "Negyedév": 90,
        "Fél év": 180,
        "Év": 365,
    }
    now = datetime.utcnow()
    cutoffs = {
        label: now - timedelta(days=days)
        for label, days in periods.items()
    }
    oldest_cutoff = min(cutoffs.values())

    products = Product.query.filter_by(user_id=current_user.id).all()
    products_by_id = {product.id: product for product in products}
    histories_by_product = {}

    if products_by_id:
        history_rows = (
            db.session.query(
                PriceHistory.product_id,
                PriceHistory.checked_at,
                PriceHistory.price,
            )
            .join(Product, Product.id == PriceHistory.product_id)
            .filter(
                Product.user_id == current_user.id,
                PriceHistory.checked_at >= oldest_cutoff,
            )
            .order_by(
                PriceHistory.product_id.asc(),
                PriceHistory.checked_at.asc(),
                PriceHistory.id.asc(),
            )
            .all()
        )
        for product_id, checked_at, price in history_rows:
            histories_by_product.setdefault(product_id, []).append((checked_at, price))

    period_results = {
        label: {"activity": [], "changes": []}
        for label in periods
    }

    for product_id, product in products_by_id.items():
        history = histories_by_product.get(product_id, [])
        if len(history) < 2:
            continue

        timestamps = [checked_at for checked_at, _ in history]
        for label, cutoff in cutoffs.items():
            start = bisect_left(timestamps, cutoff)
            period_history = history[start:]
            if len(period_history) < 2:
                continue

            prices = [price for _, price in period_history]
            change_count = sum(
                previous != current
                for previous, current in zip(prices, prices[1:])
            )
            period_results[label]["activity"].append(
                {"name": product.name, "size": product.size, "count": change_count}
            )

            first_price, latest_price = prices[0], prices[-1]
            if first_price is not None and latest_price is not None:
                period_results[label]["changes"].append(
                    {
                        "name": product.name,
                        "size": product.size,
                        "change": latest_price - first_price,
                    }
                )

    stats = {}
    for label, result in period_results.items():
        activity = sorted(
            result["activity"], key=lambda item: item["count"], reverse=True
        )[:5]
        changes = result["changes"]
        stats[label] = {
            "top5": activity,
            "up": sorted(
                (item for item in changes if item["change"] > 0),
                key=lambda item: item["change"],
                reverse=True,
            )[:5],
            "down": sorted(
                (item for item in changes if item["change"] < 0),
                key=lambda item: item["change"],
            )[:5],
        }

    return render_template("stats.html", stats=stats)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if (
            user
            and user.username != LEGACY_USERNAME
            and check_password_hash(user.password_hash, password)
        ):
            session.clear()
            session["user_id"] = user.id
            return redirect(url_for("index"))

        flash("Hibás felhasználónév vagy jelszó.")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("A felhasználónév és a jelszó megadása kötelező.")
            return render_template("register.html")

        existing = User.query.filter_by(username=username).first()
        if existing:
            flash("Ez a felhasználónév már foglalt.")
            return render_template("register.html")

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.commit()

        session.clear()
        session["user_id"] = user.id
        flash("Sikeres regisztráció.")
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    return redirect(url_for("login"))


# RUN


if __name__ == "__main__":
    import os

    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")

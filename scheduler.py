from apscheduler.schedulers.background import BackgroundScheduler

from models import db, Variant, PriceHistory

from scraper import scrape_product


scheduler = BackgroundScheduler()


def update_prices():
    print("Árfrissítés indult...")

    variants = Variant.query.all()

    for variant in variants:
        try:
            data = scrape_product(variant.url)

            if not data:
                continue

            for item in data["variants"]:
                if item["url"] != variant.url:
                    continue

                if not item["price"]:
                    continue

                variant.in_stock = item["in_stock"]

                latest = (
                    PriceHistory.query.filter_by(variant_id=variant.id)
                    .order_by(PriceHistory.checked_at.desc())
                    .first()
                )

                if latest is None or latest.price != item["price"]:
                    db.session.add(
                        PriceHistory(variant_id=variant.id, price=item["price"])
                    )

            db.session.commit()

        except Exception as e:
            print("Scheduler error:", e)


def start_scheduler():
    scheduler.add_job(update_prices, trigger="interval", hours=24)

    scheduler.start()

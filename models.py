from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(255), nullable=False)

    brand = db.Column(db.String(100), index=True)

    concentration = db.Column(db.String(100))

    size = db.Column(db.String(100))

    url = db.Column(db.Text, nullable=False, unique=True)

    image_url = db.Column(db.Text)

    in_stock = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    prices = db.relationship("PriceHistory", backref="product", lazy=True)

    restocked_at = db.Column(db.DateTime, nullable=True)


class PriceHistory(db.Model):
    __tablename__ = "price_history"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"))
    price = db.Column(db.Integer, nullable=True)
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)

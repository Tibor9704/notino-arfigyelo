import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(BASE_DIR, 'database', 'perfumes.db')}",
    ).strip()

    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgres://", "postgresql://", 1
        )

    # This project installs psycopg2-binary, not psycopg 3.
    if SQLALCHEMY_DATABASE_URI.startswith("postgresql+psycopg://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgresql+psycopg://", "postgresql+psycopg2://", 1
        )

    if "?pgbouncer=true" in SQLALCHEMY_DATABASE_URI:
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("?pgbouncer=true", "")

    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY:
        if os.environ.get("APP_ENV", "").lower() == "production":
            raise RuntimeError("SECRET_KEY must be set in production")
        SECRET_KEY = "development-only-key-change-before-deploying"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

# Perfume Price Tracker

A Flask-based perfume price tracker that monitors Notino product links, stores historical price data, and now supports per-user watchlists.

## What it does

* Add perfumes by product URL
* Automatically imports all available size variants
* Stores historical prices for each tracked product
* Detects price increases and decreases
* Highlights significant discounts
* Tracks restocks
* Shows product history charts
* Lets each logged-in user view only their own tracked products
* Includes a simple built-in login/register flow
* Supports deployment to Render with PostgreSQL on Supabase

## Current architecture

* Flask app for the web UI and request handling
* SQLAlchemy ORM for persistence
* PostgreSQL-compatible database support via `DATABASE_URL`
* Render-ready Gunicorn startup
* Built-in user account system with session-based authentication
* Legacy watchlist compatibility preserved through a `legacy` internal user

## Deployment notes

For online deployment, set these environment variables on Render:

* `DATABASE_URL` — Supabase Postgres connection string
* `SECRET_KEY` — a secure random secret

The app is ready to run with:

```bash
gunicorn app:app
```

## Tech stack

* Python
* Flask
* Flask-SQLAlchemy
* PostgreSQL / SQLite compatible configuration
* Jinja2
* Chart.js
* Gunicorn

## Notes

This project is intended for educational and personal use, and is now structured for per-user watchlists in a hosted deployment setup.

## License

This project is intended for educational and personal use.

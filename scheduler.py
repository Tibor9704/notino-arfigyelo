from apscheduler.schedulers.background import BackgroundScheduler

from run_update import run_update


scheduler = BackgroundScheduler(timezone="UTC")


def update_prices():
    """Run the same product based updater used by the command line entry point."""
    run_update()


def start_scheduler():
    if scheduler.running:
        return

    scheduler.add_job(
        update_prices,
        trigger="interval",
        hours=24,
        id="update_prices",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()

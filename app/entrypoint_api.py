import sentry_sdk
import uvicorn

from app.config import Settings
from app.logging import setup_logging

settings = Settings.get()

if settings.app.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.app.sentry_dsn,
        environment=settings.app.env,
        traces_sample_rate=0.2,
    )

setup_logging(debug=settings.app.env == "development")

# Действует в ЭТОМ процессе. При `reload=True` дочерний импортирует только
# `create_app`, и в нём остаётся loguru по умолчанию — без перехвата и Sentry.
# Это дев-режим, в проде `reload=False`. Нужен JSON и под перезагрузкой —
# `setup_logging` переезжает в `create_app`, но его начнут звать и тесты.

if __name__ == "__main__":
    uvicorn.run(
        "app.interface.api.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        reload=settings.app.env != "production",
    )

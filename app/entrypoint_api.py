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

# Настройка выше действует в ЭТОМ процессе. При `reload=True` uvicorn поднимает
# дочерний, и импортирует в нём только строку `app.interface.api.app:create_app`
# — этот модуль туда не попадает, поэтому в обслуживающем процессе остаётся
# приёмник loguru по умолчанию, без перехвата stdlib и без Sentry. Это ровно
# дев-режим, где читаемый вывод и нужен, а Sentry обычно выключен. В проде
# `reload=False`: uvicorn работает в этом же процессе, и настройка действует.
# Понадобится JSON-лог и под перезагрузкой — `setup_logging` переезжает в
# `create_app`, но тогда его начнут звать и тесты, поднимающие приложение.

if __name__ == "__main__":
    uvicorn.run(
        "app.interface.api.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        reload=settings.app.env != "production",
    )

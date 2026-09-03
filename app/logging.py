"""Перехват stdlib-логов в loguru.

Уровень WARNING, а не NOTSET: httpx пишет INFO-строку с полным URL на каждый
запрос, а секрет в query-string у чужих API — норма. Падения taskiq и
SQLAlchemy идут через `logging.error` и доходят.

UVICORN СЮДА НЕ ПОПАДАЕТ: свой `LOGGING_CONFIG` с `propagate: False` он
применяет после нас, поэтому в проде рядом с JSON идут его текстовые строки.
Забрать — `log_config=None` в `entrypoint_api.py`, ценой access-лога (он INFO).
"""

import inspect
import json
import logging
import sys
import traceback
from typing import TYPE_CHECKING

import sentry_sdk
from loguru import logger

if TYPE_CHECKING:
    from types import FrameType

    import loguru

_LIBRARY_CAPTURE_LEVEL = logging.WARNING


class _InterceptHandler(logging.Handler):
    """Переливает stdlib-логи в loguru: иначе строка уходит через `lastResort`
    в stderr, без имени задачи и мимо JSON. Симптом — «в Sentry ошибка есть,
    а в логе воркера пусто».
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Идём С САМОГО `emit`, пока не выйдем за пределы `logging`:
        # `logging.currentframe()` дал бы кадр внутри этого файла, и местом
        # вызова у каждой строки оказался бы `logging.callHandlers`.
        frame: FrameType | None = inspect.currentframe()
        depth = 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.bind(source=record.name).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _json_sink(message: "loguru.Message") -> None:
    """JSON от сериализатора, а не от шаблона: `{message}` подставляется без
    экранирования, и кавычка или перевод строки в чужом сообщении ломает
    строку. У httpx и трейсбеков они штатны. `source` — чей это лог.
    """
    record = message.record
    payload = {
        "time": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["extra"].get("source") or record["name"],
        "message": record["message"],
    }
    exc = record["exception"]
    if exc is not None and exc.type is not None:
        payload["exception"] = "".join(
            traceback.format_exception(exc.type, exc.value, exc.traceback)
        )
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _sentry_sink(message: "loguru.Message") -> None:
    """ERROR+ в Sentry, но только СВОИ: `source` стоит у чужих перехваченных,
    а об их аварии уже сообщил `SentryMiddleware.on_error`. Без отсечки на одну
    аварию шло бы два события — сообщает тот, у кого на руках исключение.
    """
    record = message.record
    if record["extra"].get("source"):
        return
    exc = record["exception"]
    if exc is not None and exc.value is not None:
        sentry_sdk.capture_exception(exc.value)
    else:
        sentry_sdk.capture_message(record["message"], level="error")


def setup_logging(debug: bool = False) -> None:
    """DEBUG в цвете и текстом либо INFO в JSON; Sentry — если инициализирован."""
    logger.remove()

    # Перехват ДО приёмников: корневой обработчик один, дальше `propagate`.
    logging.basicConfig(handlers=[_InterceptHandler()], level=_LIBRARY_CAPTURE_LEVEL, force=True)

    if not debug:
        logger.add(_json_sink, level="INFO", backtrace=False, diagnose=False)
    else:
        logger.add(
            sys.stdout,
            level="DEBUG",
            format=(
                "<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level>"
                " | <cyan>{name}</cyan> - {message}"
            ),
            colorize=True,
            backtrace=True,
            diagnose=True,
        )

    if sentry_sdk.is_initialized():
        logger.add(_sentry_sink, level="ERROR", backtrace=False, diagnose=False)

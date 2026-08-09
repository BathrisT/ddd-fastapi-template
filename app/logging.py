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

# Уровень, с которого забираем чужие логи. НЕ NOTSET — и это не про шум.
#
# httpx на КАЖДЫЙ запрос пишет INFO-строку с полным URL, а секреты в URL —
# норма у половины чужих API: ключ или подпись параметром запроса, обмен
# токенов с `client_secret` и `refresh_token` в query-string, подписанные
# ссылки на хранилище. Опустив корневой уровень до нуля, мы вывалили бы их в
# stdout и дальше в агрегатор — навсегда и в открытом виде, причём для
# исходящих, которых сегодня в проекте ещё нет.
#
# Ради чего перехват заводился, при этом сохраняется: taskiq сообщает о падении
# задачи через `logging.error`, SQLAlchemy о своих — тоже, и обе доходят.
#
# UVICORN СЮДА НЕ ПОПАДАЕТ, и это надо знать. `uvicorn.run()` применяет свой
# `LOGGING_CONFIG` уже после нас, а там у логгера `uvicorn` стоит
# `propagate: False` со своим обработчиком (`uvicorn.error` — его потомок и
# упирается в то же). До корневого обработчика записи не доходят никогда,
# поэтому в проде рядом с JSON-строками идут обычные текстовые строки uvicorn.
# Забрать их можно `log_config=None` в `uvicorn.run` (`entrypoint_api.py`) —
# ценой access-лога: он INFO, а корневой уровень здесь WARNING, и access-строки
# просто пропадут. Размен осознанный и оставлен проекту: шаблон не решает за
# него, что важнее — единый формат или журнал обращений.
_LIBRARY_CAPTURE_LEVEL = logging.WARNING


class _InterceptHandler(logging.Handler):
    """Переливает stdlib-логи в loguru.

    Без этого весь чужой код пишет мимо: taskiq на падении задачи зовёт
    `logging.error("Exception found while executing function: %s")`, и строка
    уходит через `lastResort` в stderr — без имени задачи, без привязанного к
    записи контекста, ломая JSON для агрегатора. То же у SQLAlchemy (про
    uvicorn см. оговорку у `_LIBRARY_CAPTURE_LEVEL` — он до сюда не доходит).
    Симптом «в Sentry ошибка есть, а в логе воркера пусто» — про это.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Ищем кадр, из которого звали stdlib-логгер. Начинать надо С САМОГО
        # `emit` и пропускать кадры, ПОКА не выйдем за пределы `logging`:
        # `logging.currentframe()` вернул бы кадр внутри этого файла, условие
        # «мы всё ещё в logging» было бы ложно сразу, и место вызова
        # определялось бы как `logging.callHandlers` у КАЖДОЙ строки.
        frame: FrameType | None = inspect.currentframe()
        depth = 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        # `record.name` — единственное, что говорит, ЧЕЙ это лог (`taskiq...`,
        # `uvicorn.error`). У loguru своё поле имени, и без переноса
        # библиотека была бы неотличима от нашего кода.
        logger.bind(source=record.name).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _json_sink(message: "loguru.Message") -> None:
    """Строка лога как JSON, собранный сериализатором, а не шаблоном.

    Формат-строкой это делать нельзя: `{message}` подставляется в шаблон без
    экранирования, и любая кавычка или перевод строки внутри сообщения ломает
    JSON. Пока в лог шли только свои строки, это почти не проявлялось; с
    перехватом чужих проявляется всегда — у httpx и uvicorn кавычки в тексте
    штатны (`"HTTP/1.1 200 OK"`), а у трейсбека переводы строк.
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
    """Forward ERROR+ loguru records to Sentry — но только СВОИ.

    `source` в extra проставляет `_InterceptHandler`, то есть он стоит ровно у
    перехваченных чужих записей, и их сюда пускать нельзя. Падение задачи уже
    отправляет `SentryMiddleware.on_error` — с самим исключением и контекстом
    задачи; следом taskiq пишет о том же происшествии через `logging.error`, и
    без этой отсечки Sentry получал бы ДВА события на одну аварию:
    `capture_exception` от middleware и `capture_message` отсюда, в разных
    группах, с задвоенными счётчиками алертов и вдвое быстрее выбранной квотой.
    Сообщать должен тот, у кого на руках исключение, а не тот, кто прочитал о
    нём строку.
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
    """Configure loguru.

    debug=True  — DEBUG level, human-readable, coloured
    debug=False — INFO level, JSON for log aggregators

    Sentry sink is attached automatically when Sentry has already been
    initialised (i.e. sentry_sdk.init() was called in the entrypoint).
    """
    logger.remove()

    # Перехват ДО добавления приёмников: корневой обработчик один, дальше
    # `propagate=True` доводит до него записи всех библиотечных логгеров.
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

    # Attach Sentry sink only when Sentry has been initialised by the entrypoint
    if sentry_sdk.is_initialized():
        logger.add(_sentry_sink, level="ERROR", backtrace=False, diagnose=False)

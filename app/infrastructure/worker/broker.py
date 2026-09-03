"""Транспорт очереди — и больше ничего.

Про задачи, арендаторов и базу брокер не знает: обработчики вешает реестр,
зависимости отдаёт контейнер, и то и другое снаружи. Настройки аргументом, а
не глобально на импорте — иначе импорт приложения требовал бы полного
окружения. Модульная функция здесь законна: CLI taskiq ждёт готовый объект
по пути `app.entrypoint_worker:broker`.
"""

from taskiq import AsyncBroker
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from app.config import Settings
from app.infrastructure.worker.middleware import SentryMiddleware

# Задавать обязательно: у `RedisAsyncResultBackend` умолчание — хранить всё и
# без TTL, а ресивер пишет результат каждого выполнения. Ключи копились бы
# вечно в том же Redis, где очередь и замки, и память положила бы заодно их.
# Сутки, а не час: протухший раньше окна опроса результат неотличим от сбоя.
_RESULT_TTL_S = 60 * 60 * 24


def build_broker(settings: Settings) -> AsyncBroker:
    return (
        ListQueueBroker(settings.redis.url)
        .with_middlewares(SentryMiddleware())
        .with_result_backend(
            RedisAsyncResultBackend(settings.redis.url, result_ex_time=_RESULT_TTL_S)
        )
    )

"""Транспорт очереди — и больше ничего.

Брокер знает, как положить сообщение в Redis и как его оттуда достать. Про
задачи, арендаторов и базу он не знает: обработчики вешает реестр, зависимости
отдаёт контейнер, и то и другое — снаружи, в точке входа.

Настройки приходят аргументом, а не читаются глобально на импорте: иначе
`import app.interface.api.app` требовал бы полного окружения, и тесты не
собирались бы. Модульная функция здесь законна и перечислена в
`module_functions_allowed` — CLI taskiq запускается как
`taskiq worker app.entrypoint_worker:broker` и ждёт готовый объект по пути,
поэтому сам объект точка входа обязана держать на уровне модуля.

Всё, что здесь висело сверх этого — движок, фабрика сессий и шифр в двух
глобалах с ленивой инициализацией через `WORKER_STARTUP`, — было самодельным
процессным скоупом; его место занял `ProcessProvider`.
"""

from taskiq import AsyncBroker
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from app.config import Settings
from app.infrastructure.worker.middleware import SentryMiddleware

# Сколько живёт результат выполненной задачи. Задавать обязательно: у
# `RedisAsyncResultBackend` по умолчанию `keep_results=True` и НИ ОДНОГО TTL,
# а ресивер пишет результат КАЖДОГО выполнения — включая те, которых никто не
# спрашивал (в шаблоне это все: порт `JobResults` не читает никто, обработчики
# возвращают `None`). Ключи копились бы вечно в том же Redis, где лежит сама
# очередь и замки `KeyGuard`, — и кончившаяся память положила бы заодно их.
#
# Сутки, а не час: TTL обязан быть заведомо больше самого долгого окна опроса.
# Протухший раньше времени результат — это прогресс-бар, которому успешно
# отработавшая задача отвечает «результата нет», и отличить это от сбоя
# вызывающий не может. Сутки с запасом перекрывают любую операцию, исход
# которой вообще имеет смысл спрашивать.
_RESULT_TTL_S = 60 * 60 * 24


def build_broker(settings: Settings) -> AsyncBroker:
    return (
        ListQueueBroker(settings.redis.url)
        .with_middlewares(SentryMiddleware())
        .with_result_backend(
            RedisAsyncResultBackend(settings.redis.url, result_ex_time=_RESULT_TTL_S)
        )
    )

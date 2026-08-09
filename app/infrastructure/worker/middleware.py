from typing import Any

import sentry_sdk
from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult


class SentryMiddleware(TaskiqMiddleware):
    """Контекст упавшей задачи в событии Sentry.

    Контекст ставится ЗДЕСЬ, на своём форке скоупа, а не заранее в
    `pre_execute` — и это не стиль. `asyncio.create_task` копирует контекст по
    ССЫЛКЕ, поэтому все одновременно исполняемые задачи воркера делят один
    объект `Scope`. Пока `welcome_user` секундами ждёт ответа модели,
    стартовавший рядом `purge_inactive_users` перетирал бы имя транзакции, тег
    и контекст — и упавшая первая уезжала бы в Sentry под чужим именем и с
    чужими аргументами. Дежурный пошёл бы разбирать не тот обработчик и не
    того пользователя, то есть middleware вредил бы ровно в том, ради чего
    написан.

    Форк на каждую задачу умеет делать `AsyncioIntegration`, но здесь она
    молча не сработает: она патчит фабрику задач у УЖЕ РАБОТАЮЩЕГО цикла, а
    `sentry_sdk.init` в точке входа выполняется на импорте, когда цикла ещё
    нет. Интеграция, которая тихо не подхватилась, хуже её отсутствия.

    `new_scope()` же безопасен по построению: внутри блока нет ни одного
    `await`, поэтому вклиниться между установкой тегов и отправкой другой
    задаче нечем.

    Цена: события, отправленные ВО ВРЕМЯ выполнения задачи не отсюда
    (например, `logger.error` из сценария через sentry-приёмник loguru), тега
    задачи не несут. Раньше несли — но с равными шансами чужой.
    """

    def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
        exception: BaseException,
    ) -> None:
        with sentry_sdk.new_scope() as scope:
            scope.set_transaction_name(message.task_name)
            scope.set_tag("task_name", message.task_name)
            scope.set_context(
                "taskiq",
                {
                    "task_id": message.task_id,
                    "task_name": message.task_name,
                    "args": message.args,
                    "kwargs": message.kwargs,
                    "labels": message.labels,
                },
            )
            sentry_sdk.capture_exception(exception)

from typing import Any

import sentry_sdk
from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult


class SentryMiddleware(TaskiqMiddleware):
    """Контекст упавшей задачи в событии Sentry.

    Ставится ЗДЕСЬ, на своём форке скоупа: `create_task` копирует контекст по
    ССЫЛКЕ, и соседняя задача перетирала бы имя и аргументы. Внутри блока нет
    ни одного `await`, поэтому вклиниться в него нечем.
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

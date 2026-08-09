"""Идентификатор записи расписания обязан быть стабильным между опросами.

Шедулер taskiq опрашивает источник расписания заново на каждом проходе цикла и
держит по `schedule_id` две защиты: «эту крон-задачу в текущей минуте уже
отправляли» и «предыдущая отправка ещё идёт». Обе — множества ключей, и на
идентификаторе, который генерируется при каждом опросе (а по умолчанию это
`uuid4()`), обе молчат.

Тест сторожит не гипотезу, а конкретный отказ: при опросе чаще раза в минуту
(`taskiq scheduler --interval`) каждый опрос внутри совпавшей минуты отправит
задачу заново. Заметить это в шаблоне сегодня нельзя — единственная задача
расписания накрыта замком `KeyGuard`, и дубль съедается. У второй такой удачи
может не быть.
"""

from app.interface.worker.schedule import SCHEDULE, Schedule


def test_schedule_id_is_stable_between_polls() -> None:
    first = {task.task_name: task.schedule_id for task in Schedule.scheduled_tasks()}
    second = {task.task_name: task.schedule_id for task in Schedule.scheduled_tasks()}

    assert first == second


def test_schedule_id_is_unique_per_entry() -> None:
    ids = [task.schedule_id for task in Schedule.scheduled_tasks()]

    assert len(set(ids)) == len(SCHEDULE)


def test_schedule_id_is_derived_from_handler_and_cron() -> None:
    """Один обработчик, поставленный на два времени, — две записи, а не одна.

    Поэтому в идентификатор входит и расписание: выведи его из одного имени —
    и вторая строка с тем же обработчиком молча слилась бы с первой, потеряв
    одно из двух срабатываний.
    """
    for item, task in zip(SCHEDULE, Schedule.scheduled_tasks(), strict=True):
        assert item.handler.__name__ in task.schedule_id
        assert item.cron in task.schedule_id

"""Общая половина проверки входа: предъявленное → опознанный звонящий.

Достать доказательство — дело входа (заголовок у HTTP, `message.kwargs` у
очереди), превратить его в личность — дело общее и живёт здесь. Оставь
резолюцию в HTTP-гейте, и второй вход либо продублирует её, либо позовёт гейт
оттуда, где нет `Request`.

Проверка тут намеренно тривиальная: это шов, а не система аутентификации. У
настоящего проекта здесь разбор JWT или поход к провайдеру — меняется ТЕЛО
метода, форма вокруг (гейт, `Caller`, `AuthError`) остаётся.
"""

from loguru import logger

from app.application.dto.caller import Caller
from app.domain.exceptions import AuthError


class CallerResolver:
    def __init__(self, expected_key: str) -> None:
        self._expected = expected_key

    def resolve(self, presented: str | None) -> Caller:
        """Опознать звонящего или отказать.

        Пустой ключ — вход не проверяется, но каждый пропуск идёт в лог:
        незакрытые ручки в проде выглядят как работающие.
        """
        if not self._expected:
            logger.warning("caller_resolver: APP__API_KEY пуст — запрос пропущен без проверки")
            return Caller(name="anonymous")

        # Сравнение обычное, а не постоянного времени: ключ высокоэнтропийный,
        # перебор невозможен. Появится ключ из пароля — менять вместе с ним.
        if presented != self._expected:
            raise AuthError("Неверный ключ доступа")

        return Caller(name="service")

"""Резолюция звонящего: три ветки, и одна из них — про безопасность.

Ветка «пустой ключ пропускает всех» проверяется здесь и больше нигде: в
интеграционных тестах ключ задан, и её никто не проходит. Сломать её молча —
значит либо закрыть шаблон так, что он перестанет подниматься из коробки, либо
открыть проект, который считал себя закрытым.
"""

import pytest
from loguru import logger

from app.application.dto.caller import Caller
from app.application.services.caller_resolver import CallerResolver
from app.domain.exceptions import AuthError


async def test_matching_key_identifies_the_caller():
    assert CallerResolver("secret").resolve("secret") == Caller(name="service")


@pytest.mark.parametrize("presented", [None, "", "wrong"])
async def test_anything_but_the_key_is_refused(presented):
    """Отсутствие заголовка — такой же отказ, как неверный ключ.

    `None` приходит, когда заголовка нет вовсе, и обрабатывать его отдельно
    было бы дырой: «не предъявил» не мягче, чем «предъявил чужое».
    """
    with pytest.raises(AuthError):
        CallerResolver("secret").resolve(presented)


async def test_empty_expected_key_lets_everyone_in_but_says_so():
    """Шаблон обязан подниматься из коробки — но не молча.

    Незакрытые ручки в проде выглядят точно так же, как работающие, и узнают
    о них последними. Поэтому пропуск обязан оставлять след.
    """
    said: list[str] = []
    sink = logger.add(said.append, level="WARNING")
    try:
        caller = CallerResolver("").resolve(None)
    finally:
        logger.remove(sink)

    assert caller == Caller(name="anonymous")
    assert any("APP__API_KEY" in line for line in said)

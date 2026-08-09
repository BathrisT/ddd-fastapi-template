"""Инварианты заведения пользователя.

Тест без единого дублёра — ни репозитория, ни коммиттера, ни публикатора. В
этом и смысл того, что правило живёт у сущности: проверять его можно, ничего
не собирая, и оно действует на любом входе, а не только в том сценарии, где
про него вспомнили.
"""

import pytest

from app.domain.exceptions import ValidationError
from app.domain.models.user import User


def test_email_is_normalized():
    """«Ann@» и «ann@» — один человек.

    Уникальный индекс это НЕ поймает: для базы это разные строки, и в таблице
    окажутся двое, а заметят это через месяц.
    """
    user = User.register(" Ann@Example.COM ", "Аня")

    assert user.email == "ann@example.com"


def test_name_is_trimmed():
    assert User.register("ann@example.com", "  Аня  ").name == "Аня"


def test_blank_name_is_rejected():
    """Пробелы — не имя, хотя схему с `min_length=1` они проходят."""
    with pytest.raises(ValidationError):
        User.register("ann@example.com", "   ")


def test_registered_user_is_not_saved_yet():
    """`id=0` говорит «ещё не сохранён» вслух (CLAUDE.md, «Идентификаторы»)."""
    user = User.register("ann@example.com", "Аня")

    assert user.id == 0
    assert user.created_at is None
    assert user.is_active is False

"""Пользователь: демонстрационная сущность шаблона и шов через все слои —
модель, порт, сценарий, событие, обработчик, расписание. Ненужное удаляют по
следу `rg -l welcome app tests migrations`.
"""

from dataclasses import dataclass
from datetime import datetime

from app.domain.exceptions import ValidationError


@dataclass
class User:
    # `0` — «ещё не сохранён». Умолчания нет намеренно: `id=0` говорят вслух,
    # иначе несохранённое и сохранённое на месте вызова неотличимы.
    id: int
    email: str
    name: str
    # Неактивный — не подтвердивший почту; уборка сносит только таких.
    is_active: bool = False
    # `None` до первого сохранения: момент ставит база, а не приложение.
    created_at: datetime | None = None
    # Пишется фоновой задачей; `None` до неё — норма, а не ошибка.
    # В НАСТОЯЩЕМ ПРОЕКТЕ полю здесь не место: текст от модели ничего не
    # разрешает и не запрещает, его дом — своя таблица. Оставлен ради трёх
    # демонстраций разом (идемпотентность, свой commit, автономный журнал).
    welcome_message: str | None = None

    @staticmethod
    def register(email: str, name: str) -> "User":
        """Завести пользователя сразу валидным.

        Правило живёт ЗДЕСЬ, а не в сценарии: второй вход завёл бы мимо него.
        Нормализация почты не косметика — «Ann@» и «ann@» для базы разные
        строки, и уникальный индекс двойника не поймает.
        """
        cleaned = name.strip()
        if not cleaned:
            raise ValidationError("Имя не может быть пустым")
        return User(id=0, email=email.strip().lower(), name=cleaned)

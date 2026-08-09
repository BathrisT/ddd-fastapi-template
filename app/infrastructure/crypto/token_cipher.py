"""Шифрование секретов, лежащих в базе (токены доступа, чужие API-ключи).

Что шифровать — решает репозиторий поля, а не этот класс. Одно правило общее:
значение, по которому ИЩУТ строку, шифровать нельзя — шифротекст Fernet
недетерминирован, и поиск по нему не найдёт ничего.

Ключ: APP__FERNET_KEY (base64-urlsafe, 32 байта). Сгенерировать:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Пустой ключ — режим «насквозь»: значения хранятся как есть, приложение
поднимается без настроенного шифра. Для прода это не вариант, и по обратной
причине тоже: включить шифр позже нельзя без миграции данных — уже лежащий
открытый текст `decrypt` не переживёт.
"""

from cryptography.fernet import Fernet


class TokenCipher:
    def __init__(self, fernet_key: str) -> None:
        self._fernet = Fernet(fernet_key.encode()) if fernet_key else None

    def encrypt(self, value: str) -> str:
        if self._fernet is None:
            return value
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        if self._fernet is None:
            return value
        return self._fernet.decrypt(value.encode()).decode()

    def encrypt_nullable(self, value: str | None) -> str | None:
        return self.encrypt(value) if value is not None else None

    def decrypt_nullable(self, value: str | None) -> str | None:
        return self.decrypt(value) if value is not None else None

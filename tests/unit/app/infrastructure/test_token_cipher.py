from app.infrastructure.crypto.token_cipher import TokenCipher

# Валидный ключ Fernet: base64 от 32 байт.
_KEY = "dGVzdC1mZXJuZXQta2V5LTMyLWJ5dGVzLWxvbmchISE="


def test_roundtrip():
    cipher = TokenCipher(_KEY)

    assert cipher.decrypt(cipher.encrypt("секрет")) == "секрет"


def test_ciphertext_is_not_deterministic():
    """Fernet подмешивает соль и время, поэтому два шифра одного значения
    разные. Отсюда правило: по зашифрованному полю НЕЛЬЗЯ искать строку —
    равенство в WHERE не совпадёт никогда."""
    cipher = TokenCipher(_KEY)

    assert cipher.encrypt("секрет") != cipher.encrypt("секрет")


def test_empty_key_is_pass_through():
    """Пустой ключ — режим «насквозь», чтобы приложение поднималось без шифра.

    Обратной дороги нет: включить шифр позже, не мигрировав уже лежащий
    открытый текст, невозможно — `decrypt` его не переживёт."""
    cipher = TokenCipher("")

    assert cipher.encrypt("секрет") == "секрет"
    assert cipher.decrypt("секрет") == "секрет"


def test_nullable_helpers_pass_none_through():
    cipher = TokenCipher(_KEY)

    assert cipher.encrypt_nullable(None) is None
    assert cipher.decrypt_nullable(None) is None
    assert cipher.decrypt_nullable(cipher.encrypt_nullable("секрет")) == "секрет"

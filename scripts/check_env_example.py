"""`.env.example` не расходится с настройками.

Расхождение не даёт ни красного теста, ни отказа линтера — оно даёт падение
при СТАРТЕ у того, кто склонировал репозиторий: автор правки не узнает, у него
значение уже лежит в `.env`. Обратное расхождение тише и хуже: строка осталась,
поля нет, её продолжают заполнять, и она выглядит настройкой, которой
управляют.

Поля берутся ИМПОРТОМ `Settings`, а не разбором синтаксиса: тот пришлось бы
учить наследованию, псевдонимам и `Annotated`. Compose-файлы разбираются тоже —
они читают тот же файл, а до `Settings` их переменные не доходят.
"""

import re
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT))
from app.config import Settings  # noqa: E402

EXAMPLE = ROOT / ".env.example"
COMPOSE_GLOB = "docker-compose*.y*ml"

# `${VAR}`, `${VAR:-default}`, `${VAR-default}`, `${VAR:?message}` — все формы
# подстановки, которые понимает compose. Вторая группа — модификатор, по нему
# видно, есть ли значение по умолчанию.
BRACED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)((?::?[-?])[^}]*)?\}")
# Голое `$VAR`. `${` сюда не попадает: `{` не подходит под первый символ имени.
BARE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


class EnvNames:
    @staticmethod
    def of(model: type[BaseModel], prefix: str, delimiter: str) -> dict[str, bool]:
        """`{ИМЯ ПЕРЕМЕННОЙ: обязательна ли}` для модели и её вложенных."""
        names: dict[str, bool] = {}
        for field, info in model.model_fields.items():
            annotation = info.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                names.update(
                    EnvNames.of(annotation, f"{prefix}{field.upper()}{delimiter}", delimiter)
                )
            else:
                names[f"{prefix}{field.upper()}"] = info.is_required()
        return names

    @staticmethod
    def declared() -> dict[str, bool]:
        config = Settings.model_config
        prefix = str(config.get("env_prefix") or "").upper()
        delimiter = str(config.get("env_nested_delimiter") or "__")
        return EnvNames.of(Settings, prefix, delimiter)


class Example:
    @staticmethod
    def keys() -> set[str]:
        if not EXAMPLE.is_file():
            print(f"ОШИБКА: нет {EXAMPLE.name} — сверять не с чем.", file=sys.stderr)
            raise SystemExit(2)
        found: set[str] = set()
        for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            found.add(stripped.split("=", 1)[0].strip().upper())
        return found


class Compose:
    """Переменные, которые читает docker-compose, а не приложение.

    Проброс порта — не поле конфига. Сторож ругался на них каждый раз, и
    каждый раз ему отвечали «так и надо»: такую проверку перестают читать.
    """

    @staticmethod
    def used() -> set[str]:
        """Имена, которые compose подставляет, по всем compose-файлам.

        Значение по умолчанию поблажки не даёт: оно ещё и прячет ручку,
        потому что без неё всё работает — просто не так, как человек думает.
        """
        found: set[str] = set()
        for path in sorted(ROOT.glob(COMPOSE_GLOB)):
            # `$$` в compose — экранированный доллар, литерал `$`, а не
            # подстановка. Убираем до разбора, иначе `$$HOME` приедет именем.
            text = path.read_text(encoding="utf-8").replace("$$", "")
            found.update(name.upper() for name, _ in BRACED.findall(text))
            found.update(name.upper() for name in BARE.findall(text))
        return found


def main() -> int:
    declared = EnvNames.declared()
    listed = Example.keys()
    compose = Compose.used()

    # На ЛЮБОМ отсутствующем поле, а не только на обязательном: полей без
    # умолчания единицы, и проверка раз в год — это предупреждение, которое
    # перестают читать.
    missing = sorted(name for name in declared if name not in listed)
    # Compose тоже читает этот файл — переменную, которую подставляет он,
    # устаревшей считать нельзя. Остаётся то, что не читает НИКТО: строка
    # выглядит настройкой, её продолжают заполнять, и она не делает ничего.
    stale = sorted(listed - set(declared) - compose)
    # Обратное: compose подставляет переменную, а в примере её нет. Значение по
    # умолчанию не оправдание — оно прячет ручку надёжнее, чем её отсутствие:
    # без неё всё работает, просто не так, как думает тот, кто её не видел.
    undocumented = sorted(compose - listed - set(declared))

    if missing or undocumented:
        if missing:
            print("Поля настроек, которых нет в .env.example:")
            for name in missing:
                mark = " (обязательное — без него старт падает)" if declared[name] else ""
                print(f"  {name}{mark}")
        if undocumented:
            print("Переменные, которые подставляет docker-compose, а примера для них нет:")
            for name in undocumented:
                print(f"  {name}")
        print("\nДопиши строки в .env.example: он документирует весь конфиг целиком.")
        return 1

    if stale:
        print("В .env.example есть, а не читает никто — ни настройки, ни compose:")
        for name in stale:
            print(f"  {name}")
        return 0

    print(
        f"Env example: {len(declared)} переменных настроек и "
        f"{len(compose)} compose-переменных описаны. OK."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

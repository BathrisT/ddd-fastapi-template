"""`.env.example` не расходится с настройками.

    poetry run python scripts/check_env_example.py

Расхождение здесь не даёт ни красного теста, ни отказа линтера. Оно даёт
падение при СТАРТЕ у того, кто склонировал репозиторий и заполнил пример:
добавили обязательное поле в `Settings`, забыли строку в примере — и новый
разработчик (или деплой) получает `ValidationError` на пустом месте, а автор
правки об этом не узнает, потому что у него в `.env` значение уже лежит.

Обратное расхождение тише и потому хуже: строка осталась в примере, а поля
больше нет. Её продолжают заполнять, значение уезжает в окружение и не делает
ничего — но выглядит как настройка, которой управляют.

Поля берутся импортом `Settings`, а не разбором синтаксиса. Разбор пришлось бы
учить наследованию, псевдонимам и `Annotated`, и он разошёлся бы с pydantic на
первой же нестандартной записи; импорт спрашивает у самой модели. Создавать
объект настроек при этом не нужно — читается описание полей, а не значения,
поэтому переменные окружения скрипту не требуются.

Имя переменной собирается по правилам самой модели: префикс и разделитель
берутся из `model_config`, а не вписаны сюда константой.
"""

import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT))
from app.config import Settings  # noqa: E402

EXAMPLE = ROOT / ".env.example"


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


def main() -> int:
    declared = EnvNames.declared()
    listed = Example.keys()

    # Отказ на ЛЮБОМ отсутствующем поле, а не только на обязательном. Полей без
    # значения по умолчанию в типовом конфиге единицы (здесь два из двадцати
    # четырёх), и проверка, срабатывающая раз в год, — это предупреждение,
    # которое перестают читать. К тому же ручка, которой нет в примере,
    # невидима всем, кто не открывал config.py: она как бы есть и как бы нет.
    missing = sorted(name for name in declared if name not in listed)
    # Ключи примера, которых нет в настройках. Часть из них законна: файл
    # читают не только настройки приложения — docker-compose берёт оттуда же
    # свои переменные. Поэтому это предупреждение, а не отказ: список коротких
    # ложных срабатываний, который каждый раз объясняют заново, кончается тем,
    # что проверку выключают целиком.
    stale = sorted(listed - set(declared))

    if missing:
        print("Поля настроек, которых нет в .env.example:")
        for name in missing:
            mark = " (обязательное — без него старт падает)" if declared[name] else ""
            print(f"  {name}{mark}")
        print("\nДопиши строки в .env.example: он документирует весь конфиг целиком.")
        return 1

    if stale:
        print("В .env.example есть, а в настройках нет (проверь, не устарело ли):")
        for name in stale:
            print(f"  {name}")
        return 0

    print(f"Env example: все {len(declared)} переменных настроек описаны. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

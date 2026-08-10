"""Храповик покрытия: ниже достигнутого не опускаемся.

    poetry run python scripts/check_coverage.py

Зовётся из цели `test` сразу после прогона — по данным, которые pytest-cov уже
записал в `.coverage`. Тесты пересчитывать не нужно, скрипт только читает.

**Почему храповик, а не постоянный порог.** Порог вида `--cov-fail-under=80`
оставляет зазор между собой и фактическим числом, и зазор этот бесплатный:
пока факт выше порога, непокрытый код добавляется без единого возражения, и
никто этого не видит. У человека такой зазор выедается годами, у агента —
за неделю, потому что код прибывает быстрее, чем внимание к тестам.

**Почему число в отдельном файле, а не в pyproject.** Понижение обязано быть
заметным. Строка среди трёхсот строк конфига в ревью не читается; отдельный
файл в одну строку даёт дифф, мимо которого не пройти, и приезжает в пакет
ревью-гейта. Файл обязан быть закоммичен — в этом весь смысл.

**Скрипт умеет только повышать.** Понизить можно, но исключительно руками,
правкой этого файла. Причины для понижения бывают законные — например,
добавили модуль, который по замыслу покрывается интеграционным тестом, а не
unit'ом, — но это решение человека, и оно должно быть видно, а не случиться
само по себе из зелёного прогона.

Число берётся через API coverage, а не через `coverage report --format=total`:
API отдаёт точный float, тогда как текстовый отчёт округляет до `precision` из
конфига (по умолчанию до целых), и просадка с 80.9 до 80.1 прошла бы незаметно.
Конфигурацию (в частности `omit`) API читает из pyproject сам, поэтому область
измерения у скрипта и у отчёта одна и та же.
"""

import io
import sys
from pathlib import Path

import coverage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import ROOT  # noqa: E402

BASELINE = ROOT / ".coverage-baseline"
DATA_FILE = ROOT / ".coverage"

# До сотых: полный float даёт разный хвост от прогона к прогону (порядок
# сборки данных параллельных воркеров), и файл переписывался бы на каждом
# запуске без единого реального изменения.
_PRECISION = 2

_HEADER = (
    "# Достигнутое покрытие (`make test`). Скрипт только повышает.\n"
    "# Понижение — руками и с объяснением: оно означает, что тестов стало\n"
    "# меньше, чем было, и это решение человека, а не побочный эффект прогона.\n"
)


class Baseline:
    @staticmethod
    def read() -> float | None:
        """Число из файла, или None если файла ещё нет."""
        if not BASELINE.is_file():
            return None
        for line in BASELINE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                return float(stripped)
            except ValueError:
                print(
                    f"ОШИБКА: в {BASELINE.name} ожидалось число, а лежит {stripped!r}.",
                    file=sys.stderr,
                )
                raise SystemExit(2) from None
        print(f"ОШИБКА: в {BASELINE.name} нет числа.", file=sys.stderr)
        raise SystemExit(2)

    @staticmethod
    def write(value: float) -> None:
        BASELINE.write_text(f"{_HEADER}{value:.{_PRECISION}f}\n", encoding="utf-8")


class Measured:
    @staticmethod
    def total() -> float:
        """Текущий процент из данных последнего прогона.

        Отсутствие `.coverage` — отказ, а не ноль: скрипт зовут после тестов,
        и пустые данные значат, что прогон не состоялся. Ноль в этом месте
        читался бы как «покрытие обвалилось» и запутал бы того, кто увидит
        отказ.
        """
        if not DATA_FILE.is_file():
            print(
                f"ОШИБКА: нет {DATA_FILE.name} — прогон с покрытием не делался.\n"
                "Скрипт зовётся после `make test`, сам тесты не запускает.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        cov = coverage.Coverage()
        cov.load()
        return round(cov.report(file=io.StringIO()), _PRECISION)


def main() -> int:
    current = Measured.total()
    recorded = Baseline.read()

    if recorded is None:
        Baseline.write(current)
        print(
            f"Coverage: заведён {BASELINE.name} = {current:.{_PRECISION}f}%. "
            "Закоммить его — дальше опускаться ниже нельзя."
        )
        return 0

    if current < recorded:
        print(
            f"Coverage: покрытие просело: было {recorded:.{_PRECISION}f}%, "
            f"стало {current:.{_PRECISION}f}% (-{recorded - current:.{_PRECISION}f})."
        )
        print(
            f"\nПокрой написанное тестами. Если просадка осознанная — например, "
            f"добавлен модуль, покрываемый интеграционно, — правь "
            f"{BASELINE.name} руками и скажи в отчёте, почему: сам скрипт "
            "понижать не будет."
        )
        return 1

    if current > recorded:
        Baseline.write(current)
        print(
            f"Coverage: {recorded:.{_PRECISION}f}% -> {current:.{_PRECISION}f}%. "
            f"{BASELINE.name} поднят, добавь его в коммит."
        )
        return 0

    print(f"Coverage: {current:.{_PRECISION}f}%, не ниже достигнутого. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

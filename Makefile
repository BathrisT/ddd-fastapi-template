# Recipes use bash syntax (for/if loops in lint-check). On Windows make defaults
# to cmd.exe, which can't parse them ("f was unexpected at this time"). Point it at
# Git Bash instead. On Linux/CI the default /bin/sh handles the POSIX recipes fine.
ifeq ($(OS),Windows_NT)
# Путь к Git Bash. Автопоиска нет и быть не может: списки make разделяются
# пробелами, и путь с пробелом словом не бывает — `$(wildcard C:/Program?Files/
# .../bash.exe)` шаблон-то раскрывает, но результат тут же режется по пробелу и
# от него остаётся `C:/Program`. Поэтому одно прямое присваивание (его make не
# режет) и возможность переопределить.
#
# Git стоит не здесь? Передай свой путь:
#   make GIT_BASH="C:/Users/.../AppData/Local/Programs/Git/usr/bin/bash.exe" check
# либо задай GIT_BASH один раз в переменных окружения.
# Где обычно: winget и scoop кладут в %LOCALAPPDATA%/Programs/Git, старые
# сборки — в "C:/Program Files (x86)/Git", MSYS2 — в C:/msys64.
#
# Промах пути make не назовёт: он молча возьмёт `sh.exe` из PATH, а из
# PowerShell, где каталога Git в PATH нет, доедет до cmd.exe и упадёт на
# «f was unexpected at this time» — про шелл в этом сообщении ни слова.
GIT_BASH ?= C:/Program Files/Git/usr/bin/bash.exe
SHELL := $(GIT_BASH)
.SHELLFLAGS := -c
endif

APP_DIR = ./app
TEST_DIR = ./tests

.PHONY: lint lint-check layout-check interface-check effects-check env-check query-check migrations-check typecheck layers layers-show layers-report schema-check test test-unit test-integration check bandit precommit review-pack install init template-diff template-update template-graft

lint:
	poetry run ruff check $(APP_DIR) $(TEST_DIR) --fix $(ARGS)
	poetry run ruff format $(APP_DIR) $(TEST_DIR) $(ARGS)

lint-check: layout-check interface-check effects-check env-check query-check migrations-check
	poetry run python scripts/check_not_initialised.py
	poetry run ruff check $(APP_DIR) $(TEST_DIR) $(ARGS)
	poetry run ruff format $(APP_DIR) $(TEST_DIR) --check $(ARGS)
	poetry run python scripts/check_escape_hatches.py
# Пометки-побеги (`# ruff: noqa` на весь файл, мок вместо дублёра) проверяет
# скрипт, а не конвейер `rg`: ripgrep не предустановлен ни на одной ОС, и без
# него ОБЕ строки завершались нулём — сторож против молчаливого обхода правил
# сам молчаливо обходился. `grep` не спасает: в Windows его тоже нет.

# Раскладка кода: где что лежит и какого размера. CLAUDE.md, «Раскладка кода».
layout-check:
	poetry run python scripts/check_module_functions.py
	poetry run python scripts/check_file_length.py
	poetry run python scripts/check_layer_folders.py
	poetry run python scripts/check_duplicated_constants.py
	poetry run python scripts/check_class_shape.py
	poetry run python scripts/check_use_cases.py

# Границы: что имеет право лежать в routes/, откуда хендлер берёт зависимости,
# не потерялся ли вход по дороге и кто разговаривает с базой. Вход здесь —
# любой протокол, не только HTTP: обработчик очереди тоже вход.
# Правило: docs/rules/композиция-и-скоупы.md
interface-check:
	poetry run python scripts/check_fastapi_routes.py
	poetry run python scripts/check_package_coverage.py
	poetry run python scripts/check_composition.py
	poetry run python scripts/check_entrypoint_registry.py
	poetry run python scripts/check_db_access.py

# Порядок операций в пути записи: публикация после фиксации и работа с
# возвращённым из репозитория объектом. Единственная проверка не про форму
# кода, а про последовательность — и единственный класс ошибок, который тесты
# не ловят по конструкции (NoopEventPublisher задач не создаёт).
effects-check:
	poetry run python scripts/check_effects.py

# Конфиг: каждое поле Settings описано в .env.example. Расхождение не даёт ни
# красного теста, ни отказа линтера — оно даёт падение при старте у того, кто
# склонировал репозиторий, тогда как у автора правки значение уже лежит в .env.
env-check:
	poetry run python scripts/check_env_example.py

# Стоимость чтения: N+1 запросов. Отказ только на квадратичном росте (чтение
# во вложенном цикле по безграничному источнику); линейный рост — предупреждение,
# потому что бывает уместен, а решает это автор, а не сторож.
query-check:
	poetry run python scripts/check_n_plus_one.py

# Одна голова у цепочки ревизий. Две головы ломают `alembic upgrade head`, но не
# дают ни красного теста, ни отказа линтера: слияние, которое их породило,
# проходит чисто — миграции с разных сторон не конфликтуют, файлы-то разные.
# Базы не требует: heads читаются из каталога ревизий.
migrations-check:
	poetry run python scripts/check_migration_heads.py

typecheck:
	poetry run mypy $(APP_DIR) $(ARGS)

layers:
	poetry run tach check $(ARGS)

layers-show:
	poetry run tach show --web $(ARGS)

# Аргумент передаётся через `ARGS=` (иначе make примет его за вторую цель) и
# является ПУТЁМ, а не именем модуля: `make layers-report ARGS=app/domain`.
layers-report:
	poetry run tach report $(ARGS)

schema-check:
	poetry run python scripts/check_schema_consistency.py

# Покрытие гейтит ТОЛЬКО полный прогон (unit+integration): `test-unit` в
# одиночку меряется против всего app/, включая слой входа, который покрывается
# интеграционными тестами, — его сырой процент структурно занижен и регрессию
# не показывает.
#
# Постоянного порога нет: достигнутое лежит в `.coverage-baseline`, и сторож
# сам поднимает планку, когда покрытие выросло. Опускать её умеет только
# человек — правкой файла, видимой в диффе.
test:
	poetry run pytest $(TEST_DIR) -n auto --cov=$(APP_DIR) --cov-report=term:skip-covered -q $(ARGS)
	poetry run python scripts/check_coverage.py

test-unit:
	poetry run pytest $(TEST_DIR)/unit -n auto --cov=$(APP_DIR) --cov-report=term:skip-covered -q $(ARGS)

test-integration:
	poetry run pytest $(TEST_DIR)/integration -n auto -q $(ARGS)

bandit:
	poetry run bandit -r $(APP_DIR) -q -c pyproject.toml $(ARGS)

check: lint-check typecheck layers test-unit

precommit: lint-check typecheck layers schema-check test bandit

# Пакеты для ревью-гейта: по файлу на проход линзы (весь дифф с окружением,
# у каждого прохода свой порядок разделов) плюс журналы линз. Собирается перед
# каждым раундом ревью — дерево изменилось, значит пакеты устарели.
review-pack:
	poetry run python .claude/hooks/build_pack.py .

# ─── Шаблон ──────────────────────────────────────────────────────────────────
# Правило целиком: docs/rules/шаблон-и-обновления.md

# Первое, что делают после `git clone`: имя проекта на место имени шаблона,
# `origin` прочь, если он ведёт в шаблон. Коммит НЕ делается — человек смотрит
# дифф. Пропустить нельзя: `check_not_initialised.py` держит `lint-check`
# красным, пока имя проекта совпадает с именем шаблона.
#
# Здесь голый интерпретатор, а не `poetry run`: цель зовут ДО `make install`,
# когда окружения ещё нет. Скрипту хватает стандартной библиотеки.
#
# Имя интерпретатора подставляется, а не угадывается, и это не педантизм: на
# Linux и macOS PEP 394 гарантирует `python3`, а `python` во многих
# дистрибутивах просто отсутствует; на Windows ровно наоборот — установщик с
# python.org кладёт `python`, а `python3` там либо нет вовсе, либо это
# заглушка, открывающая Microsoft Store. Ошибиться тут дороже всего: это самая
# первая команда после клонирования, и «python: команда не найдена» — всё, что
# человек успеет узнать о шаблоне.
ifeq ($(OS),Windows_NT)
INIT_PY = python
else
INIT_PY = python3
endif

init:
	$(INIT_PY) scripts/init_project.py --name "$(NAME)"

# Что придёт и во что обойдётся: входящие коммиты, расхождение в обе стороны и
# предсказание конфликтов (git merge-tree, рабочее дерево не трогается).
template-diff:
	poetry run python scripts/template_sync.py --diff

# Слияние в ветку-буфер `template-update`. Зависит от `check`: в красное дерево
# не сливаем, иначе после слияния не разобрать, чья краснота.
template-update: check
	poetry run python scripts/template_sync.py --update

# Разовая прививка шаблона к проекту, который начинался не из него.
template-graft:
	poetry run python scripts/template_sync.py --graft

install:
	poetry install
	poetry run pre-commit install

# Recipes use bash syntax (for/if loops in lint-check). On Windows make defaults
# to cmd.exe, which can't parse them ("f was unexpected at this time"). Point it at
# Git Bash instead. On Linux/CI the default /bin/sh handles the POSIX recipes fine.
ifeq ($(OS),Windows_NT)
SHELL := C:/Program Files/Git/usr/bin/bash.exe
.SHELLFLAGS := -c
endif

APP_DIR = ./app
TEST_DIR = ./tests

.PHONY: lint lint-check layout-check interface-check typecheck layers layers-show layers-report schema-check test test-unit test-integration check bandit precommit review-pack migrate install infra-up infra-down api worker scheduler

lint:
	poetry run ruff check $(APP_DIR) $(TEST_DIR) --fix $(ARGS)
	poetry run ruff format $(APP_DIR) $(TEST_DIR) $(ARGS)

lint-check: layout-check interface-check
	poetry run ruff check $(APP_DIR) $(TEST_DIR) $(ARGS)
	poetry run ruff format $(APP_DIR) $(TEST_DIR) --check $(ARGS)
	@for f in $$(rg -l --glob="*.py" "# ruff: noqa" $(APP_DIR)); do \
		if ! rg -q "# allow-ruff-noqa:" $$f; then \
			echo "ERROR: $$f disables ruff for entire file. Add '# allow-ruff-noqa: <reason>' in the file."; \
			exit 1; \
		fi; \
	done
	@if rg -n --glob="*.py" "MagicMock|mock\.patch|unittest\.mock" $(APP_DIR) | rg -qv "^[^:]+:[^:]+:.*#.*allow-mock:"; then \
		echo "ERROR: mock/patch found in app/. Mocks belong in tests/ only."; \
		exit 1; \
	fi
	@if rg -n --glob="*.py" "# noqa$$|# noqa " $(APP_DIR) $(TEST_DIR) | rg -qv "# noqa: "; then \
		echo "ERROR: bare '# noqa' found — specify error code(s), e.g. '# noqa: E501'."; \
		exit 1; \
	fi

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
# Правило: docs/правила/композиция-и-скоупы.md
interface-check:
	poetry run python scripts/check_fastapi_routes.py
	poetry run python scripts/check_package_coverage.py
	poetry run python scripts/check_composition.py
	poetry run python scripts/check_entrypoint_registry.py
	poetry run python scripts/check_db_access.py

typecheck:
	poetry run mypy $(APP_DIR) $(ARGS)

layers:
	poetry run tach check $(ARGS)

layers-show:
	poetry run tach show --web $(ARGS)

layers-report:
	poetry run tach report $(ARGS)

schema-check:
	poetry run python scripts/check_schema_consistency.py

# Порог гейтит ТОЛЬКО полный прогон (unit+integration): `test-unit` в одиночку
# меряется против всего app/, включая слой входа, который покрывается
# интеграционными тестами, — его сырой процент структурно занижен и
# регрессию не показывает. Число поднимают, когда покрытие реально выросло,
# и никогда — чтобы разблокировать красный прогон.
test:
	poetry run pytest $(TEST_DIR) -n auto --cov=$(APP_DIR) --cov-report=term:skip-covered --cov-fail-under=80 -q $(ARGS)

test-unit:
	poetry run pytest tests/unit -n auto --cov=$(APP_DIR) --cov-report=term:skip-covered -q $(ARGS)

test-integration:
	poetry run pytest tests/integration -n auto -q $(ARGS)

bandit:
	poetry run bandit -r $(APP_DIR) -q -c pyproject.toml $(ARGS)

check: lint-check typecheck layers test-unit

precommit: lint-check typecheck layers schema-check test bandit

# Пакеты для ревью-гейта: по файлу на проход линзы (весь дифф с окружением,
# у каждого прохода свой порядок разделов) плюс журналы линз. Собирается перед
# каждым раундом ревью — дерево изменилось, значит пакеты устарели.
review-pack:
	poetry run python .claude/hooks/build_pack.py .

migrate:
	poetry run alembic upgrade head

install:
	poetry install
	poetry run pre-commit install

# ─── Local dev ───────────────────────────────────────────────────────────────

infra-up:
	docker compose -f docker-compose.local.yml up -d

infra-down:
	docker compose -f docker-compose.local.yml down

API_PORT = 8000

# Добивание прошлого процесса перед запуском — Windows-специфика: перезапуск на
# рабочей машине делают этими же целями, и оставшийся слушатель порта даёт
# «address already in use». На остальных ОС строки обязаны исчезнуть, а не
# просто не падать: префикс `-` глушит КОД ВОЗВРАТА, но `powershell: command not
# found` всё равно печатается, а без него make останавливает цель — и `make api`
# из README на Linux не доходит до запуска вовсе, сообщая при этом про
# powershell, а не про причину. `:` — встроенный no-op шелла, съедающий свои
# аргументы вместе с кавычками.
ifeq ($(OS),Windows_NT)
WIN_ONLY =
else
WIN_ONLY = :
endif

api:
	-$(WIN_ONLY) powershell -Command "Get-NetTCPConnection -LocalPort $(API_PORT) -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $$_.OwningProcess -Force -ErrorAction SilentlyContinue }"
	$(WIN_ONLY) powershell -Command "Start-Sleep -Seconds 1"
	poetry run python -m app.entrypoint_api

# `--max-async-tasks` тот же, что в docker-compose.yml, и по той же причине:
# дефолт 100 против пула БД в 10+5 означает, что 85 задач ждут pool_timeout и
# падают, а ретраев в брокере нет намеренно. Локальный запуск берёт пул из того
# же `.env`, так что расходиться этим двум командам не с чего.
worker:
	-$(WIN_ONLY) powershell -Command "Get-WmiObject Win32_Process -Filter \"name='python.exe'\" | Where-Object { $$_.CommandLine -like '*entrypoint_worker*' } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force -ErrorAction SilentlyContinue }"
	$(WIN_ONLY) powershell -Command "Start-Sleep -Seconds 1"
	poetry run taskiq worker app.entrypoint_worker:broker --max-async-tasks 10

scheduler:
	-$(WIN_ONLY) powershell -Command "Get-WmiObject Win32_Process -Filter \"name='python.exe'\" | Where-Object { $$_.CommandLine -like '*scheduler*' } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force -ErrorAction SilentlyContinue }"
	$(WIN_ONLY) powershell -Command "Start-Sleep -Seconds 1"
	poetry run taskiq scheduler app.entrypoint_scheduler:scheduler

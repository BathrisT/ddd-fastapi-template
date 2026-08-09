# ddd-fastapi-template

Шаблон backend-проекта: FastAPI + Taskiq + SQLAlchemy, слои DDD, контейнер
зависимостей и набор сторожей, которые не дают раскладке расползтись.

Правила проекта — в [CLAUDE.md](CLAUDE.md), правило про композицию и скоупы —
в [docs/правила/композиция-и-скоупы.md](docs/правила/композиция-и-скоупы.md).

## Ветки

| Ветка | Что в ней |
|---|---|
| `master` | базовый одноарендный скелет. Всё общее живёт здесь |
| `multitenant` | `master` + арендатор: провайдеры на вход, репозитории в скоупе арендатора, фан-аут по расписанию |

Правка общего идёт **только в `master`**, дальше `git checkout multitenant && git merge master`.
Обратной дороги нет: арендатор в `master` не едет никогда. Специализированные
шаблоны (интеграции, боты) ответвляются от той из двух, что ближе.

## Быстрый старт

```bash
cp .env.example .env      # заполнить APP__FERNET_KEY и пароли
make install              # poetry install + pre-commit install
make infra-up             # postgres на 5433 и redis на 6379 в docker
make migrate
make api                  # http://localhost:8000/health
make worker               # в отдельном терминале
make scheduler            # в отдельном терминале
```

Целиком в докере (api + worker + scheduler + миграции + postgres + redis):

```bash
docker compose up -d --build
```

## Что внутри

Три точки входа — HTTP, воркер очереди и шедулер, — собранные из **одного**
контейнера: процессный скоуп (пулы, движок, шифр, клиент модели) и привходовой
(сессия, `Committer`, репозитории, сценарии).

Демонстрационная сущность `User` проходит все слои и показывает шов целиком:

```
POST /users → RegisterUserUseCase → UserRepo → commit
                                  → UserRegistered
                                  → EventRouter → очередь
                                  → welcome_user → WelcomeUserUseCase → AiService
раз в час  → purge_inactive_users → замок KeyGuard → PurgeInactiveUsersUseCase
```

Что из этого удалять, заводя настоящий проект, — в конце CLAUDE.md.

## Проверки

```bash
make check       # lint + типы + слои + unit-тесты (быстро, без docker)
make precommit   # то же + сверка ORM с миграциями + integration + bandit
```

Сверх линтера и тайпчекера гоняются сторожа раскладки (`scripts/check_*.py`):
функции уровня модуля, длина файла, роли подпапок, форма классов, дубли
констант, форма сценариев, содержимое `routes/`, покрытие пакетов агрегатором,
`Depends` вне гейтов, реестр обработчиков очереди и кто открывает сессию БД.
Все они читают конфиг из `pyproject.toml` — переносятся в проект с другой
раскладкой без правки кода.

`make precommit` требует Docker: `schema-check` поднимает одноразовый Postgres,
прогоняет миграции и спрашивает у alembic, совпадают ли с ними ORM-модели.

## Ревью-гейт

`.claude/` содержит PreToolUse-хук, который блокирует `git commit`, пока правки
в `app/**/*.py` от 20 строк не пройдут ревью тремя линзами. Работает только в
сессиях Claude Code; механизм описан в самом `review_gate.py`.

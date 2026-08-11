"""Процессный скоуп: то, что дорого создать и что не знает про арендатора.

Живёт до рестарта, общее для всех запросов и задач. Ничего из этого не имеет
права держать состояние арендатора — иначе первый пришедший определит объект
для всех остальных, молча (правило 1 композиции).

Соблазн, от которого этот файл и защищает: процессный скоуп, собранный дважды
и по-разному — в `lifespan` веб-приложения и самодельно в модуле брокера, через
глобалы и хук `WORKER_STARTUP`. Расхождение потом выглядит как «на API общий
пул, а в задачах каждая открывает свой».

Правило целиком — docs/rules/композиция-и-скоупы.md
"""

from collections.abc import AsyncIterator

import httpx
from dishka import Provider, Scope, from_context, provide
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.application.ports.key_guard import KeyGuard
from app.application.ports.repositories.welcome_attempt_repo import WelcomeAttemptRepo
from app.application.ports.services.ai_service import AiService
from app.application.services.caller_resolver import CallerResolver
from app.config import Settings
from app.infrastructure.crypto.token_cipher import TokenCipher
from app.infrastructure.db.autonomous_session import AutonomousSession
from app.infrastructure.db.repositories.welcome_attempt_repo import SqlWelcomeAttemptRepo
from app.infrastructure.redis.key_guard import RedisKeyGuard
from app.infrastructure.services.openai_service import OpenAiService


class ProcessProvider(Provider):
    scope = Scope.APP

    # Настройки приходят снаружи: композиция не лезет за глобальным
    # состоянием, а точка входа и так их уже читает
    settings = from_context(provides=Settings, scope=Scope.APP)

    @provide
    async def engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        engine = create_async_engine(
            settings.database.url,
            pool_size=settings.database.pool_size,
            max_overflow=settings.database.max_overflow,
            pool_recycle=settings.database.pool_recycle,
            pool_pre_ping=True,
        )
        yield engine
        await engine.dispose()

    @provide
    def session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        """`expire_on_commit=False` — не стиль, а необходимость: с `True` любое
        обращение к атрибуту ORM-объекта после коммита дёргает ленивую
        подгрузку, а в async это `MissingGreenlet`, то есть падение на ровном
        месте.

        `autoflush=False` — против неявного SQL. С автосбросом `add()` не шлёт
        ничего, а висящая вставка уезжает внутри СЛЕДУЮЩЕГО запроса через
        сессию, каким бы он ни был: `IntegrityError` поднимается из строки,
        которая сама ничего не писала, и мимо `except` того репозитория, что
        эту вставку делал. Читать свои записи внутри транзакции это не мешает:
        репозитории делают `flush()` явно — им и так нужен присвоенный базой
        идентификатор.
        """
        return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @provide
    def autonomous(self, engine: AsyncEngine) -> AutonomousSession:
        """Короткие транзакции, которые обязаны пережить сессию сценария.

        Процессная: внутри свой engine на NullPool, состояния арендатора не
        держит.
        """
        return AutonomousSession(engine)

    @provide
    async def http(self) -> AsyncIterator[httpx.AsyncClient]:
        """Один пул на процесс.

        Процессным он может быть ровно до тех пор, пока не знает про
        арендатора: `base_url` тут не задаётся, адрес и токен живут в
        привходовой обёртке над клиентом. Клиент с вшитым адресом арендатора
        процессным быть не имеет права (правило 1 композиции).
        """
        async with httpx.AsyncClient() as client:
            yield client

    @provide
    async def redis(self, settings: Settings) -> AsyncIterator[Redis]:
        """Тоже пул, и тоже один.

        `Redis.from_url` по месту вызова — это незакрытый пул на каждый
        запрос; в процессном скоупе вопрос «закрывать ли» не возникает.
        """
        client: Redis = Redis.from_url(settings.redis.url, decode_responses=True)
        yield client
        await client.aclose()

    # Журнал процессный, а не привходовой: своя короткая транзакция на
    # своём engine, от сессии сценария не зависит вовсе.
    journal = provide(SqlWelcomeAttemptRepo, provides=WelcomeAttemptRepo)

    @provide
    def caller_resolver(self, settings: Settings) -> CallerResolver:
        return CallerResolver(settings.app.api_key)

    @provide
    def cipher(self, settings: Settings) -> TokenCipher:
        return TokenCipher(settings.app.fernet_key)

    @provide
    async def ai(self, settings: Settings) -> AsyncIterator[AiService]:
        """Свой HTTP-клиент внутри — значит, его надо закрыть (см. сервис)."""
        service = OpenAiService(settings.llm)
        yield service
        await service.aclose()

    guard = provide(RedisKeyGuard, provides=KeyGuard)

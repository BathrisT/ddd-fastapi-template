"""Engine для автономных записей — отдельный от основного пула.

Автономная запись идёт посреди работы сценария, который уже держит соединение
основного пула. Брать второе соединение оттуда же нельзя: при всплеске все
соединения заняты держащими их запросами, и каждый ждёт «плюс одно» — пул
теряет живучесть до pool_timeout.

Поэтому такой записи даётся свой engine на том же URL с NullPool: соединение
берётся на время короткой вставки и сразу закрывается, конкуренции с основным
пулом нет. Одновременных вставок не больше, чем сценариев в полёте (их
ограничивает основной пул), так что рост числа коннектов ограничен.

Сам по себе engine никому не выдаётся: им пользуется `AutonomousSession`, и
только через неё сюда попадают. Захотелось открыть свою сессию по месту —
дверь там же.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

_engines: dict[str, AsyncEngine] = {}


class AutonomousEngine:
    @staticmethod
    def for_(main_engine: AsyncEngine) -> AsyncEngine:
        """Автономный engine для того же URL, что у основного (кэш на процесс)."""
        url = main_engine.url.render_as_string(hide_password=False)
        engine = _engines.get(url)
        if engine is None:
            engine = create_async_engine(url, poolclass=NullPool)
            _engines[url] = engine
        return engine

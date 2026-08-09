"""Реализация порта модели поверх OpenAI-совместимого API.

Промпты живут здесь, и это то самое место: они меняются вместе с моделью, а не
вместе с бизнес-правилом. Сценарий, знающий текст промпта, пришлось бы править
при каждой смене формулировки — и его тесты вместе с ним.

`base_url` из настроек, а не зашитый адрес: тем же клиентом ходят и в OpenAI, и
в совместимый шлюз, и в локальную модель — меняется строка в окружении, а не код.

HTTP-клиент здесь свой, а не общий процессный: исходящие в модель часто идут
через отдельный туннель (`LLM__PROXY`), и пул с другим маршрутом общим быть не
может в принципе. Раз клиент свой — его надо закрывать, поэтому у сервиса есть
`aclose()`, а провайдер отдаёт его генератором.
"""

import httpx
from openai import AsyncOpenAI

from app.config import LLM

_WELCOME_PROMPT = (
    "Поприветствуй нового пользователя по имени {name} одним дружелюбным "
    "предложением. Без обращения «уважаемый» и без подписи."
)


class OpenAiService:
    def __init__(self, settings: LLM) -> None:
        self._model = settings.model
        self._http = httpx.AsyncClient(proxy=settings.proxy, timeout=settings.timeout)
        self._client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            http_client=self._http,
        )

    async def welcome_text(self, name: str) -> str:
        return await self._ask(_WELCOME_PROMPT.format(name=name))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _ask(self, prompt: str) -> str:
        """Один вызов модели. Общий для всех намерений — их отличает промпт.

        Пустой ответ — отказ, а не строка. `content` приходит `None` штатно:
        сработал фильтр контента, ответ обрезан по длине, модель ушла в
        tool-call. Вернув отсюда `""`, мы отдали бы вызывающему успех: он
        сохранит пустоту, а его защита от повтора («уже есть, второй раз в
        модель не идём») закроет сценарий навсегда — с пустым приветствием, и
        в настоящем проекте с записью «отправлено» о неотправленном. Отказ же
        просто уронит задачу, и её видно в логе.
        """
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content
        if not text:
            # `RuntimeError`, а не доменный отказ: сорванный вызов модели — не
            # ответ на «что не так по существу», а сбой адаптера, ровно как
            # незарегистрированное имя задачи в `TaskiqTaskQueue.enqueue`.
            # Доменным он получил бы 400 от `exception_handlers` — код, который
            # говорит клиенту «ты неправ», хотя неправ был не он.
            raise RuntimeError(f"Модель {self._model} вернула пустой ответ")
        return text

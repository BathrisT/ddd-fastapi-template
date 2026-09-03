"""Реализация порта модели поверх OpenAI-совместимого API.

Промпты живут здесь: они меняются вместе с моделью, а не с бизнес-правилом.
`base_url` из настроек — тем же клиентом ходят в шлюз и в локальную модель.
HTTP-клиент свой, а не процессный: исходящие часто идут через отдельный
туннель (`LLM__PROXY`), и потому у сервиса есть `aclose()`.
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

        Пустой ответ — отказ, а не строка: `content` штатно приходит `None`
        (фильтр, обрезка, tool-call), и вернув `""`, мы отдали бы успех.
        Защита от повтора у вызывающего закрыла бы сценарий навсегда.
        """
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content
        if not text:
            # `RuntimeError`, а не доменный отказ: это сбой адаптера, а не
            # ответ по существу. Доменный получил бы 400 — «ты неправ»
            # клиенту, который не был неправ.
            raise RuntimeError(f"Модель {self._model} вернула пустой ответ")
        return text

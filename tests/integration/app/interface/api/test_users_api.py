"""HTTP-вход целиком: маршрут → контейнер → сценарий → база.

Ходит через настоящее приложение, поэтому ловит то, чего не поймает unit:
забытую фабрику, `route_class` без `DishkaRoute`, отказ без своего кода.
"""


async def test_health_is_up(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_register_and_list(client):
    created = await client.post("/users", json={"email": "ann@example.com", "name": "Аня"})

    assert created.status_code == 201
    body = created.json()
    assert body["id"] != 0
    assert body["is_active"] is False
    assert body["welcome_message"] is None

    listed = await client.get("/users")

    assert listed.status_code == 200
    assert [u["email"] for u in listed.json()] == ["ann@example.com"]


async def test_duplicate_email_returns_409(client):
    """Доменный `ConflictError` обязан доехать до клиента своим кодом.

    Без обработчика он превратился бы в 500, и фронт не смог бы отличить
    «почта занята» от «сервис лежит».
    """
    await client.post("/users", json={"email": "ann@example.com", "name": "Аня"})

    again = await client.post("/users", json={"email": "ann@example.com", "name": "Аня вторая"})

    assert again.status_code == 409


async def test_broken_email_returns_422(client):
    response = await client.post("/users", json={"email": "не почта", "name": "Аня"})

    assert response.status_code == 422
    # Форма тела у отказа схемы — список объектов, и её публикует OpenAPI как
    # `HTTPValidationError`. Соседний тест ниже держит вторую половину пары.
    assert isinstance(response.json()["detail"], list)


async def test_blank_name_is_a_domain_refusal_not_a_schema_one(client):
    """Отказ по существу — 400, а не 422.

    `min_length=1` пропускает `"   "`. Отдай мы 422, один код означал бы две
    несовместимые формы тела, и клиент упал бы на `detail[0].msg`.
    """
    response = await client.post("/users", json={"email": "ann@example.com", "name": "   "})

    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)

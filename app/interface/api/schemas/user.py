from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)


class UserResponse(BaseModel):
    # from_attributes — чтобы собираться прямо из доменной модели: она
    # dataclass, и переписывать поля руками в каждом маршруте значило бы
    # заводить третье место, где живёт форма ответа.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    is_active: bool
    created_at: datetime
    welcome_message: str | None

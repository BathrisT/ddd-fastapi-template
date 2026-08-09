from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

from app.interface.api.schemas.common import StatusResponse

router = APIRouter(tags=["health"], route_class=DishkaRoute)


@router.get("/health")
async def health() -> StatusResponse:
    return StatusResponse()

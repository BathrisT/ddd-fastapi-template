from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.committer import Committer


class SqlCommitter(Committer):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

from typing import Any, Callable, Generic, TypeVar
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import Select, update
from sqlalchemy.ext.asyncio import AsyncSession

# generic type vars
DBModel = TypeVar("DBModel")
ReadModel = TypeVar("ReadModel", bound=BaseModel)
CreateModel = TypeVar("CreateModel", bound=BaseModel)
UpdateModel = TypeVar("UpdateModel", bound=BaseModel)


class ListResult(Generic[ReadModel], BaseModel):
    results: list[ReadModel]


class DeleteResponse(BaseModel):
    id: UUID
    message: str


class CrudHelper(Generic[DBModel, ReadModel, CreateModel, UpdateModel]):
    def __init__(
        self,
        query_builder: Callable[[], Select[tuple[Any, ...]]],
        session: AsyncSession,
        read_model: type[ReadModel],
        create_model: type[CreateModel],
        update_model: type[UpdateModel],
        database_model: type[DBModel],
        resource_name: str = "resource",
    ):
        self.query_builder = query_builder
        self.session = session
        self.read_model = read_model
        self.create_model = create_model
        self.update_model = update_model
        self.database_model = database_model
        self.resource_name = resource_name

    async def list_response(self, **query_filters) -> ListResult[ReadModel]:
        query = self.query_builder()
        for key, value in query_filters.items():
            if value is not None:
                query = query.where(
                    getattr(query.column_descriptions[0]["type"], key) == value
                )
        result = await self.session.execute(query)
        objs = result.scalars().all()
        return ListResult(
            results=[
                self.read_model.model_validate(o, from_attributes=True) for o in objs
            ]
        )

    async def get_response(self, id: UUID) -> ReadModel:
        query = self.query_builder()
        model_class = query.column_descriptions[0]["type"]
        query = query.where(model_class.id == id)
        result = await self.session.execute(query)
        obj = result.scalar_one_or_none()
        if not obj:
            raise HTTPException(
                status_code=404, detail=f"{self.resource_name.title()} not found"
            )
        return self.read_model.model_validate(obj, from_attributes=True)

    async def create_response(self, data: CreateModel) -> ReadModel:
        obj = self.database_model(**data.model_dump())
        self.session.add(obj)
        await self.session.flush()
        return self.read_model.model_validate(obj, from_attributes=True)

    async def update_response(self, id: UUID, data: UpdateModel) -> ReadModel:
        query = self.query_builder()
        model_class = query.column_descriptions[0]["type"]

        # update directly, don't touch obj before reloading
        result = await self.session.execute(
            update(model_class)
            .where(model_class.id == id)
            .values(**data.model_dump(exclude_unset=True))
            .returning(model_class)
        )
        obj = result.scalar_one_or_none()

        if not obj:
            raise HTTPException(
                status_code=404, detail=f"{self.resource_name.title()} not found"
            )

        # refresh ensures all fields (like updated_at) are loaded from DB
        await self.session.refresh(obj)
        return self.read_model.model_validate(obj, from_attributes=True)

    async def delete_response(self, id: UUID) -> DeleteResponse:
        query = self.query_builder()
        model_class = query.column_descriptions[0]["type"]
        query = query.where(model_class.id == id)
        result = await self.session.execute(query)
        obj = result.scalar_one_or_none()

        if not obj:
            raise HTTPException(
                status_code=404, detail=f"{self.resource_name.title()} not found"
            )

        await self.session.delete(obj)
        await self.session.flush()
        return DeleteResponse(
            id=id, message=f"{self.resource_name} deleted successfully"
        )

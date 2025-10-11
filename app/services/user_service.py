from sqlalchemy import select

from app.deps.db import SessionDep
from app.models import Namespace, User


async def get_or_create_user(session: SessionDep, workos_user_id: str) -> User:
    result = await session.execute(
        select(User).where(User.workos_user_id == workos_user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(workos_user_id=workos_user_id)
        session.add(user)
        await session.flush()

        namespace = Namespace(
            user_owner_id=user.id, name=str(user.id), display_name=str(user.id)
        )
        session.add(namespace)
        await session.commit()
    return user

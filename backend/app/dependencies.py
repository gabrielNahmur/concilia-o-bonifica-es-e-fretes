from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import get_current_user, require_admin


DbSession = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DbSession) -> User:
    return get_current_user(request, db)


def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    return require_admin(user)


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(admin_user)]

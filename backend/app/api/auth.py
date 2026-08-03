from pydantic import BaseModel, EmailStr, Field
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from app.config import get_settings
from app.dependencies import CurrentUser, DbSession
from app.models import User
from app.security import COOKIE_NAME, create_access_token, hash_password, verify_password
from app.services.audit import audit


router = APIRouter(prefix="/auth", tags=["autenticação"])


class LoginInput(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordInput(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


def user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "must_change_password": user.must_change_password,
    }


@router.post("/login")
def login(payload: LoginInput, response: Response, db: DbSession):
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.lower(), User.active.is_(True)))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha inválidos")
    token = create_access_token(user)
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        max_age=settings.access_token_minutes * 60,
        path="/",
    )
    audit(db, user, "login", "session", user.id)
    db.commit()
    return user_payload(user)


@router.post("/logout")
def logout(response: Response, db: DbSession, user: CurrentUser):
    audit(db, user, "logout", "session", user.id)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser):
    return user_payload(user)


@router.post("/change-password")
def change_password(payload: ChangePasswordInput, db: DbSession, user: CurrentUser):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    audit(db, user, "change_password", "user", user.id)
    db.commit()
    return {"ok": True}

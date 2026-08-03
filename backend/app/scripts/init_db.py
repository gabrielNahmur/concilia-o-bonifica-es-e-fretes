import secrets
import string

from sqlalchemy import select

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password
from app.services.seed import seed_reference_data


def random_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value


def main():
    settings = get_settings()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_reference_data(db)
        email = settings.bootstrap_admin_email.lower()
        user = db.scalar(select(User).where(User.email == email))
        if user:
            print(f"Administrador já existe: {email}")
            return
        password = settings.bootstrap_admin_password or random_password()
        db.add(
            User(
                email=email,
                full_name=settings.bootstrap_admin_name,
                role="admin",
                active=True,
                must_change_password=True,
                password_hash=hash_password(password),
            )
        )
        db.commit()
        print(f"Administrador criado: {email}")
        print(f"Senha inicial (exibida uma única vez): {password}")


if __name__ == "__main__":
    main()

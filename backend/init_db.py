from sqlalchemy import text

from .database import get_engine
from .models import Base


def main() -> None:
    with get_engine().begin() as connection:
        version = connection.scalar(text("SHOW server_version"))
        Base.metadata.create_all(connection)
    print(f"Схема municipal_property подготовлена. PostgreSQL {version}.")


if __name__ == "__main__":
    main()

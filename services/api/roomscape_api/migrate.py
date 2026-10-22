"""``python -m roomscape_api.migrate`` — apply Alembic migrations to head."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def config() -> Config:
    return Config(str(Path(__file__).parent / "alembic.ini"))


def upgrade() -> None:
    command.upgrade(config(), "head")


if __name__ == "__main__":
    upgrade()

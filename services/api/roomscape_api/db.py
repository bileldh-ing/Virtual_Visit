from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def _dispose_after_fork() -> None:
    """Forked children (RQ work-horses) must not reuse the parent's pooled connections:
    sharing one PostgreSQL connection across processes corrupts its protocol state
    (seen as ``DuplicatePreparedStatement``). Drop the inherited pool without closing it."""
    if get_engine.cache_info().currsize:
        get_engine().dispose(close=False)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_dispose_after_fork)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def is_postgres() -> bool:
    return get_engine().dialect.name == "postgresql"

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def str_enum(*values: str, name: str) -> sa.Enum:
    """Portable enum stored as VARCHAR with a CHECK constraint (easy to extend via migration)."""
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=40)


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def fk(target: str, ondelete: str = "CASCADE", **kw) -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete=ondelete, **kw)

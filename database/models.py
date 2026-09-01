from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class SleepType(StrEnum):
    DAY = "day"
    NIGHT = "night"


class Child(Base):
    __tablename__ = "children"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    birth_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    invite_code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    silent_mode: Mapped[bool] = mapped_column(Boolean, default=False)

    users: Mapped[list["User"]] = relationship(back_populates="child", cascade="all, delete-orphan")
    sleeps: Mapped[list["SleepLog"]] = relationship(back_populates="child", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default=UserRole.MEMBER.value)
    display_name: Mapped[str] = mapped_column(String(80), default="Член семьи")

    child: Mapped[Child] = relationship(back_populates="users")


class SleepLog(Base):
    __tablename__ = "sleep_logs"
    __table_args__ = (
        Index("ix_sleep_child_start", "child_id", "start_time"),
        # На одного ребёнка физически не может существовать два активных сна.
        Index(
            "uq_sleep_one_active_per_child",
            "child_id",
            unique=True,
            sqlite_where=text("end_time IS NULL"),
            postgresql_where=text("end_time IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"))
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sleep_type: Mapped[str] = mapped_column(String(16), default=SleepType.DAY.value)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ended_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    child: Mapped[Child] = relationship(back_populates="sleeps")

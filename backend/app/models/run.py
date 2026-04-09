import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.core.database import Base


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class RunTrigger(str, enum.Enum):
    manual = "manual"
    schedule = "schedule"
    api = "api"


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id"), nullable=False)
    prefect_flow_run_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.pending)
    triggered_by: Mapped[RunTrigger] = mapped_column(Enum(RunTrigger), default=RunTrigger.manual)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bot: Mapped["Bot"] = relationship("Bot", back_populates="runs")

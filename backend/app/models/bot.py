import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), nullable=False)
    machine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("machines.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    prefect_deployment_id: Mapped[str | None] = mapped_column(String(255))
    queue_name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped["Client"] = relationship("Client", back_populates="bots")
    machine: Mapped["Machine | None"] = relationship("Machine", back_populates="bots")
    schedules: Mapped[list["Schedule"]] = relationship("Schedule", back_populates="bot")
    runs: Mapped[list["Run"]] = relationship("Run", back_populates="bot")
    alerts: Mapped[list["Alert"]] = relationship("Alert", back_populates="bot")

import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, Boolean, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.core.database import Base


class AlertChannel(str, enum.Enum):
    email = "email"
    whatsapp = "whatsapp"


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bots.id"), nullable=False)
    channel: Mapped[AlertChannel] = mapped_column(Enum(AlertChannel), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bot: Mapped["Bot"] = relationship("Bot", back_populates="alerts")

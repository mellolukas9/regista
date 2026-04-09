import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.core.database import Base


class MachineType(str, enum.Enum):
    rdp = "rdp"
    local = "local"
    cloud = "cloud"


class MachineStatus(str, enum.Enum):
    online = "online"
    offline = "offline"


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[MachineType] = mapped_column(Enum(MachineType), default=MachineType.rdp)
    queue_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[MachineStatus] = mapped_column(Enum(MachineStatus), default=MachineStatus.offline)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client: Mapped["Client"] = relationship("Client", back_populates="machines")
    bots: Mapped[list["Bot"]] = relationship("Bot", back_populates="machine")

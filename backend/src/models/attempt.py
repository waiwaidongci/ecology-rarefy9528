from datetime import datetime,timezone
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,ForeignKey,DateTime,UniqueConstraint,Index
from src.config.database import Base
class RunAttempt(Base):
 __tablename__='run_attempts'
 __table_args__=(UniqueConstraint('run_id','attempt_no'),Index('ix_run_attempts_run_id','run_id'))
 id:Mapped[int]=mapped_column(primary_key=True)
 run_id:Mapped[int]=mapped_column(ForeignKey('diversity_runs.id'))
 attempt_no:Mapped[int|None]=mapped_column(nullable=True)
 status:Mapped[str]=mapped_column(String(20),default='queued')
 token:Mapped[str]=mapped_column(String(64))
 worker:Mapped[str|None]=mapped_column(String(120),nullable=True)
 reason:Mapped[str|None]=mapped_column(String(500),nullable=True)
 recovered_from_attempt_id:Mapped[int|None]=mapped_column(nullable=True)
 artifact_path:Mapped[str|None]=mapped_column(String(300),nullable=True)
 artifact_sha256:Mapped[str|None]=mapped_column(String(64),nullable=True)
 artifact_bytes:Mapped[int|None]=mapped_column(nullable=True)
 started_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 heartbeat_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 finished_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

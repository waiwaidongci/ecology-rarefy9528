from datetime import datetime,timezone
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,JSON,ForeignKey,DateTime
from src.config.database import Base
class DiversityRun(Base):
 __tablename__='diversity_runs'
 id:Mapped[int]=mapped_column(primary_key=True)
 survey_id:Mapped[int]=mapped_column(ForeignKey('surveys.id'))
 name:Mapped[str]=mapped_column(String(100))
 status:Mapped[str]=mapped_column(String(20),default='queued')
 config:Mapped[dict]=mapped_column(JSON)
 result:Mapped[dict]=mapped_column(JSON,default=dict)
 error:Mapped[str|None]=mapped_column(String(1000),nullable=True)
 creator_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
 # Publication identity: one request key, one monotonically numbered attempt chain,
 # one published attempt at a time. Result/status/artifact evidence commit together.
 request_key:Mapped[str|None]=mapped_column(String(64),unique=True,nullable=True)
 attempt_seq:Mapped[int]=mapped_column(default=0)
 current_attempt_id:Mapped[int|None]=mapped_column(nullable=True)
 published_attempt_id:Mapped[int|None]=mapped_column(nullable=True)
 lease_expires_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 status_since:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 artifact_path:Mapped[str|None]=mapped_column(String(300),nullable=True)
 artifact_sha256:Mapped[str|None]=mapped_column(String(64),nullable=True)
 artifact_bytes:Mapped[int|None]=mapped_column(nullable=True)
 published_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)

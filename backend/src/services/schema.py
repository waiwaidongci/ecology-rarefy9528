"""Idempotent schema guard for databases created before the publication unit.

metadata.create_all creates new runs but never alters an existing
diversity_runs table, so a previously seeded volume would otherwise miss the
attempt/publication columns. Applied at startup, statements are narrow and
idempotent; migrations/002_publication_unit.sql remains the recorded shape.
"""
import logging
from sqlalchemy import inspect,text

COLUMNS={
 'request_key':'VARCHAR(64)',
 'attempt_seq':'INTEGER NOT NULL DEFAULT 0',
 'current_attempt_id':'INTEGER',
 'published_attempt_id':'INTEGER',
 'lease_expires_at':'TIMESTAMP WITH TIME ZONE',
 'status_since':'TIMESTAMP WITH TIME ZONE',
 'artifact_path':'VARCHAR(300)',
 'artifact_sha256':'VARCHAR(64)',
 'artifact_bytes':'INTEGER',
 'published_at':'TIMESTAMP WITH TIME ZONE',
}

def ensure_publication_unit(engine):
 # SQLite is used by the test suite and accepts ADD COLUMN, but the ALTER text
 # is PostgreSQL-specific; tests create the full schema from metadata instead.
 if engine.dialect.name!='postgresql':return []
 inspector=inspect(engine)
 if 'diversity_runs' not in inspector.get_table_names():return []
 existing={column['name'] for column in inspector.get_columns('diversity_runs')}
 added=[]
 for name,ddl in COLUMNS.items():
  if name in existing:continue
  try:
   with engine.begin() as connection:connection.execute(text(f'ALTER TABLE diversity_runs ADD COLUMN {name} {ddl}'))
   added.append(name)
  except Exception:logging.exception('schema_guard column %s failed',name)
 try:
  with engine.begin() as connection:connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ux_diversity_runs_request_key ON diversity_runs(request_key)'))
 except Exception:logging.exception('schema_guard index failed')
 return added

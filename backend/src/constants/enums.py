from enum import StrEnum
class Role(StrEnum):
 ADMIN='admin'
 ANALYST='analyst'
 VIEWER='viewer'
class RunStatus(StrEnum):
 QUEUED='queued'
 RUNNING='running'
 SUCCEEDED='succeeded'
 FAILED='failed'
 INTERRUPTED='interrupted'
class AttemptStatus(StrEnum):
 # Raw lifecycle values persisted in run_attempts.
 QUEUED='queued'
 RUNNING='running'
 SUCCEEDED='succeeded'
 FAILED='failed'
 INTERRUPTED='interrupted'
 SUPERSEDED='superseded'
 SKIPPED='skipped'
class AttemptDisplay(StrEnum):
 # Derived, human-facing attempt states. Never persisted.
 CLAIMING='waiting'
 RUNNING='running'
 SUCCEEDED='published'
 FAILED='failed'
 INTERRUPTED='interrupted'
 SUPERSEDED='late'
 SKIPPED='duplicate'
class NoteStatus(StrEnum):
 DRAFT='draft'
 APPROVED='approved'
 REJECTED='rejected'
class TransectStatus(StrEnum):
 ELIGIBLE='eligible'
 EMPTY='empty'

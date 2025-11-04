"""Domain models for the Job Opportunity Scanner."""

from .models import AlertRecord, Job, RawJob, SourceStatus

__all__ = ["Job", "RawJob", "AlertRecord", "SourceStatus"]

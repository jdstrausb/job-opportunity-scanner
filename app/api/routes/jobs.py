"""Job triage routes."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import database_session
from app.api.schemas import JobCounts, JobListResponse, JobTriageItem, StatusUpdateRequest
from app.domain.models import TriageStatus
from app.persistence.exceptions import RecordNotFoundError
from app.persistence.repositories import JobQueryFilters, JobRepository

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListResponse)
def list_jobs(
    match_mode: Literal["matched", "all", "unmatched"] = "matched",
    profile: Optional[str] = None,
    source_type: Optional[str] = None,
    source_identifier: Optional[str] = None,
    is_remote: Optional[bool] = None,
    user_status: Optional[TriageStatus] = None,
    alert_status: Optional[Literal["alerted", "pending_alert"]] = None,
    q: Optional[str] = None,
    sort_by: Literal["affinity_score", "posted_at", "first_seen_at"] = "affinity_score",
    sort_order: Literal["desc", "asc"] = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(database_session),
) -> JobListResponse:
    page = JobRepository(session).query_jobs(
        JobQueryFilters(
            match_mode=match_mode,
            profile=profile,
            source_type=source_type,
            source_identifier=source_identifier,
            is_remote=is_remote,
            user_status=user_status.value if user_status else None,
            alert_status=alert_status,
            q=q,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    )
    return JobListResponse(
        items=[JobTriageItem.from_record(r, include_description=False) for r in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        counts=JobCounts(**page.counts),
    )


@router.get("/{job_key}", response_model=JobTriageItem)
def get_job(job_key: str, session: Session = Depends(database_session)) -> JobTriageItem:
    record = JobRepository(session).get_triage_by_key(job_key)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_key}")
    return JobTriageItem.from_record(record, include_description=True)


@router.patch("/{job_key}/status", response_model=JobTriageItem)
def update_job_status(
    job_key: str,
    body: StatusUpdateRequest,
    session: Session = Depends(database_session),
) -> JobTriageItem:
    repo = JobRepository(session)
    try:
        repo.update_user_status(job_key, body.user_status)
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_key}") from None
    session.commit()
    record = repo.get_triage_by_key(job_key)
    assert record is not None
    return JobTriageItem.from_record(record, include_description=True)

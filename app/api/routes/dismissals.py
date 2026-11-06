"""Reactive Dismissal routes (non-destructive; see ADR-0004)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import database_session
from app.api.schemas import (
    DismissalCreateRequest,
    DismissalCreateResponse,
    DismissalDeleteResponse,
    DismissalListResponse,
    DismissedItemDTO,
)
from app.domain.models import DismissalScope
from app.persistence.repositories import DismissedItemRepository

router = APIRouter(prefix="/dismissals", tags=["dismissals"])


@router.get("", response_model=DismissalListResponse)
def list_dismissals(
    scope: Optional[DismissalScope] = None,
    q: Optional[str] = None,
    session: Session = Depends(database_session),
) -> DismissalListResponse:
    items = DismissedItemRepository(session).list_all(scope)
    if q and q.strip():
        needle = q.strip().lower()
        items = [
            i for i in items if needle in i.item_key or needle in (i.reason or "").lower()
        ]
    return DismissalListResponse(
        items=[DismissedItemDTO.from_domain(i) for i in items], total=len(items)
    )


@router.post("", status_code=201, response_model=DismissalCreateResponse)
def create_dismissal(
    body: DismissalCreateRequest, session: Session = Depends(database_session)
) -> DismissalCreateResponse:
    repo = DismissedItemRepository(session)
    item = repo.dismiss(body.item_key, body.scope, body.reason)
    hidden = repo.count_matching_jobs(item.scope, item.item_key)
    session.commit()
    return DismissalCreateResponse(item=DismissedItemDTO.from_domain(item), hidden_job_count=hidden)


@router.delete("", response_model=DismissalDeleteResponse)
def delete_dismissal(
    scope: DismissalScope,
    item_key: str = Query(..., min_length=1),
    session: Session = Depends(database_session),
) -> DismissalDeleteResponse:
    if not DismissedItemRepository(session).undismiss(item_key, scope):
        raise HTTPException(status_code=404, detail=f"No {scope.value} dismissal for '{item_key}'")
    session.commit()
    return DismissalDeleteResponse(
        restored=True, scope=scope.value, item_key=item_key.strip().lower()
    )

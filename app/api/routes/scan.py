"""Scan control routes (Alert-Safe Scan Trigger; see ADR-0004)."""

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.base import BaseAdapter
from app.api.dependencies import get_pipeline, get_scheduler
from app.api.schemas import (
    PipelineRunResultDTO,
    ScanStatusResponse,
    TriggerRequest,
    TriggerResponse,
)
from app.pipeline import ScanPipeline
from app.scheduler.service import SchedulerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scan", tags=["scan"])


@router.get("/status", response_model=ScanStatusResponse)
def scan_status(
    pipeline: ScanPipeline = Depends(get_pipeline),
    scheduler: Optional[SchedulerService] = Depends(get_scheduler),
) -> ScanStatusResponse:
    config = pipeline.app_config
    last = pipeline.last_run_result
    return ScanStatusResponse(
        is_running=pipeline.is_running,
        scan_interval=config.scan_interval,
        scan_interval_seconds=config.scan_interval_seconds or 0,
        next_run_time=scheduler.get_next_run_time() if scheduler is not None else None,
        last_run_result=(
            PipelineRunResultDTO.from_result(last, BaseAdapter._sanitize_url) if last else None
        ),
    )


def _run_scan(pipeline: ScanPipeline, send_notifications: bool) -> None:
    try:
        pipeline.run_once(send_notifications=send_notifications)
    except Exception:  # never let a background scan die silently
        logger.exception("Triggered scan failed", extra={"event": "api.scan.failed"})


@router.post("/trigger", status_code=202, response_model=TriggerResponse)
def trigger_scan(
    body: Optional[TriggerRequest] = None,
    pipeline: ScanPipeline = Depends(get_pipeline),
) -> TriggerResponse:
    """Start a background scan; defaults to not sending notifications."""
    send_notifications = body.send_notifications if body is not None else False
    if pipeline.is_running:
        raise HTTPException(status_code=409, detail="A scan is already running")

    thread = threading.Thread(
        target=_run_scan,
        args=(pipeline, send_notifications),
        name="api-triggered-scan",
        daemon=True,
    )
    thread.start()
    # Give the worker a moment to take the pipeline lock so an immediate
    # status poll reports is_running accurately.
    deadline = time.monotonic() + 1.0
    while not pipeline.is_running and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    return TriggerResponse(status="started", send_notifications=send_notifications)

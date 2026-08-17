from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..database import get_db
from ..services.reports import compute_daily_report


router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily")
def daily(
    report_date: date | None = Query(default=None, alias="date"),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    selected = report_date or datetime.now().astimezone().date()
    return compute_daily_report(connection, report_date=selected)

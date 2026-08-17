from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..database import get_db
from ..schemas import BackupCreate, BackupRestore
from ..services.backups import (
    BackupCorruptError,
    BackupError,
    BackupNotFoundError,
    create_backup,
    list_backups,
    restore_backup,
    verify_backup,
)


router = APIRouter(prefix="/backup", tags=["backup"])


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "details": {},
            "recoverable": status < 500,
        },
    )


@router.post("/create", status_code=201)
def create(
    request: BackupCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return create_backup(connection, kind=request.kind)
    except BackupError as error:
        return _error(400, "backup_create_failed", str(error))


@router.get("/list")
def catalog(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return list_backups(connection)


@router.post("/{backup_id}/verify")
def verify(
    backup_id: int,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return verify_backup(connection, backup_id)
    except BackupNotFoundError as error:
        return _error(404, "backup_not_found", str(error))
    except BackupCorruptError as error:
        return _error(409, "backup_corrupt", str(error))


@router.post("/{backup_id}/restore")
def restore(
    backup_id: int,
    request: BackupRestore,
    connection: sqlite3.Connection = Depends(get_db),
) -> Any:
    try:
        return restore_backup(connection, backup_id, dry_run=request.dry_run)
    except BackupNotFoundError as error:
        return _error(404, "backup_not_found", str(error))
    except BackupCorruptError as error:
        return _error(409, "backup_corrupt", str(error))
    except BackupError as error:
        return _error(409, "backup_restore_blocked", str(error))

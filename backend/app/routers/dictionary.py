from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..services.dictionary import DictionaryUnavailableError, lookup_dictionary


router = APIRouter(prefix="/dictionary", tags=["dictionary"])


@router.get("/lookup")
def lookup(term: str = Query(min_length=1, max_length=200)) -> dict:
    try:
        return lookup_dictionary(term)
    except DictionaryUnavailableError as error:
        raise HTTPException(503, str(error)) from error

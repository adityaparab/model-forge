"""``GET /inventory`` and ``GET /inventory/{model_id}``: the model inventory."""

from typing import Annotated

from fastapi import APIRouter, Depends
from modelforge_lineage.db import Database
from modelforge_lineage.inventory import InventoryNotFound, get_entry
from modelforge_lineage.models import InventoryEntry

from modelforge_api import queries
from modelforge_api.deps import get_db
from modelforge_api.errors import ApiError

__all__ = ["router"]

router = APIRouter(prefix="/inventory", tags=["inventory"])

Db = Annotated[Database, Depends(get_db)]


@router.get("", response_model=list[InventoryEntry])
def list_inventory(db: Db) -> list[InventoryEntry]:
    with db.connection() as conn:
        return queries.list_inventory(conn)


@router.get("/{model_id}", response_model=InventoryEntry)
def inventory_entry(model_id: str, db: Db) -> InventoryEntry:
    try:
        with db.connection() as conn:
            return get_entry(conn, model_id)
    except InventoryNotFound:
        raise ApiError(
            404, "not_found", f"model not found: {model_id}", model_id=model_id
        ) from None

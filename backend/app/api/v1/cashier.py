"""API v1 — cashier."""
from fastapi import APIRouter, Depends
from app.core.database import get_db
from app.core.security import get_current_user

router = APIRouter(prefix="/cashier")

@router.get("/")
async def list_items(db=Depends(get_db), current_user=Depends(get_current_user)):
    return {"module": "cashier", "status": "ok", "items": []}

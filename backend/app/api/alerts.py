import base64
import binascii
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select, update

from app.api.routes import valid_token
from app.core.schemas import DEVICE_ID
from app.db.models import AgentAlert, now
from app.db.session import Session
from app.modules.alerts import AlertState, alert_view, read_alerts


def authorize_alerts(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ],
):
    if not credentials or not valid_token(credentials.credentials):
        raise HTTPException(401, "Khóa truy cập không hợp lệ.")


router = APIRouter(prefix="/api/v1/alerts", dependencies=[Depends(authorize_alerts)])


def _decode(cursor: str) -> tuple[datetime, str]:
    try:
        stamp, alert_id = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        value = datetime.fromisoformat(stamp)
        if value.tzinfo is None:
            raise ValueError("Timezone is required.")
        return value, str(UUID(alert_id))
    except (ValueError, TypeError, AttributeError, binascii.Error):
        raise HTTPException(422, "Con trỏ danh sách cảnh báo không hợp lệ.") from None


@router.get("")
def list_alerts(
    response: Response,
    state: AlertState = "attention",
    cursor: Annotated[str | None, Query(max_length=160)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
):
    position = _decode(cursor) if cursor is not None else None
    with Session() as db:
        rows = read_alerts(db, state, cursor=position, limit=limit + 1)
        unread_count = db.scalar(
            select(func.count())
            .select_from(AgentAlert)
            .where(AgentAlert.device_id == DEVICE_ID, AgentAlert.acknowledged_at.is_(None))
        )
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps([last.created_at.isoformat(), last.id]).encode()
            ).decode()
        response.headers["Cache-Control"] = "no-store"
        return {
            "items": [alert_view(row) for row in rows[:limit]],
            "unread_count": unread_count,
            "next_cursor": next_cursor,
        }


@router.post("/{alert_id}/acknowledge")
def acknowledge(alert_id: UUID, response: Response):
    with Session.begin() as db:
        where = (AgentAlert.id == str(alert_id), AgentAlert.device_id == DEVICE_ID)
        db.execute(
            update(AgentAlert)
            .where(*where, AgentAlert.acknowledged_at.is_(None))
            .values(acknowledged_at=now())
        )
        row = db.scalar(select(AgentAlert).where(*where))
        if row is None:
            raise HTTPException(404, "Không tìm thấy cảnh báo.")
        response.headers["Cache-Control"] = "no-store"
        return alert_view(row)

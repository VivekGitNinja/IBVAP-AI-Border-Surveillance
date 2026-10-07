import time
from collections import defaultdict
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from backend.app.db.session import get_db
from backend.app.models.user import User
from backend.app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    needs_rehash,
    create_streaming_ticket,
)
from backend.app.services.audit import log_action
from backend.app.schemas.common import Token, UserOut
from backend.app.api.deps import current_user

router = APIRouter()

import threading

# Rate limiting for auth brute-force prevention
_auth_lock = threading.Lock()
_failed_attempts: dict[str, list[dict]] = defaultdict(list)
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_WINDOW = 900.0  # 15 minutes (900 seconds)


def _check_rate_limit(key: str):
    now = time.time()
    with _auth_lock:
        attempts = [entry for entry in _failed_attempts[key] if now - entry["timestamp"] < _LOCKOUT_WINDOW]
        _failed_attempts[key] = attempts
        if len(attempts) >= _MAX_FAILED_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Too many failed authentication attempts. Account locked for 15 minutes.",
            )


def _record_failed_attempt(key: str, ip: str = ""):
    with _auth_lock:
        _failed_attempts[key].append({"timestamp": time.time(), "ip": ip})


def _reset_attempts(key: str):
    with _auth_lock:
        _failed_attempts.pop(key, None)


@router.post("/token", response_model=Token)
@router.post("/login", response_model=Token)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Authenticate and return a JWT token with brute-force rate limiting and lockout."""
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{username}"
    _check_rate_limit(rate_key)

    u = db.query(User).filter(User.username == username, User.active == True).first()
    if not u or not verify_password(password, u.password_hash):
        _record_failed_attempt(rate_key, client_ip)
        try:
            log_action(
                db=db,
                actor=username,
                actor_role="ANONYMOUS",
                action="LOGIN_FAILED",
                target_type="user",
                target_id=username,
                details={"client_ip": client_ip, "timestamp": datetime.utcnow().isoformat()},
                ip_address=client_ip,
            )
        except Exception:
            pass
        raise HTTPException(401, "Invalid credentials")

    _reset_attempts(rate_key)
    u.last_login = datetime.utcnow()
    # Seamlessly upgrade legacy password hashes to modern 600,000 iterations upon successful login
    if needs_rehash(u.password_hash):
        u.password_hash = hash_password(password)
    db.commit()

    log_action(
        db=db,
        actor=u.username,
        actor_role=u.role,
        action="LOGIN",
        target_type="user",
        target_id=str(u.id),
        details={"client_ip": client_ip},
        ip_address=client_ip,
    )

    return {"access_token": create_access_token(u.username, u.role), "token_type": "bearer"}


class StreamTicketRequest(BaseModel):
    scope: Optional[str] = None
    camera_id: Optional[int] = None


@router.post("/stream-ticket")
def issue_stream_ticket(
    body: Optional[StreamTicketRequest] = None,
    scope: Optional[str] = Query(None, description="Target resource scope, e.g. 'camera:1', 'ws:events', 'vault'"),
    user: dict = Depends(current_user),
):
    """
    Issue a short-lived (5-minute) scoped streaming ticket for browser HTML5 video
    or WebSocket connections, avoiding exposure of long-lived general bearer credentials in URLs.
    """
    target_scope = ""
    if body and body.scope:
        target_scope = body.scope
    elif body and body.camera_id is not None:
        target_scope = f"ws:live:{body.camera_id}"
    elif scope:
        target_scope = scope

    clean_scope = target_scope.strip()
    if not clean_scope:
        raise HTTPException(status_code=400, detail="Scope cannot be empty")
    ticket = create_streaming_ticket(
        subject=user["sub"],
        scope=clean_scope,
        role=user.get("role", "VIEWER"),
        expires_seconds=300,
    )
    return {
        "stream_ticket": ticket,
        "ticket": ticket,
        "scope": clean_scope,
        "expires_in": 300,
        "token_type": "bearer",
    }


@router.get("/me", response_model=UserOut)
def get_current_user(user: dict = Depends(current_user)):
    """Get current authenticated user info."""
    return {
        "id": 0,
        "username": user["sub"],
        "role": user["role"],
        "full_name": "",
        "active": True,
    }

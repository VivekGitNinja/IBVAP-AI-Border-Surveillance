"""
IBVAP — User Administration and Session Governance Endpoints.
Restricted exclusively to system administrators (ADMIN role).
Provides user lifecycle management, password complexity enforcement,
and defense-in-depth protection for critical administrative accounts.
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.user import User
from backend.app.core.security import hash_password, role_has_permission, ROLE_HIERARCHY
from backend.app.services.audit import log_action
from backend.app.api.deps import require_permission

router = APIRouter()

TRIVIAL_PASSWORDS = {
    "admin123", "password", "password123", "12345678", "qwerty1234",
    "adminadmin", "operator123", "commander123", "surveillance123",
    "welcome123", "password@123", "admin@123"
}


def validate_password_complexity(password: str) -> None:
    """
    Enforce strict production password complexity requirements:
    - Minimum 10 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special symbol
    - Not in known trivial dictionary list
    """
    if len(password) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 10 characters long.",
        )
    if password.lower() in TRIVIAL_PASSWORDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is too common/trivial and is prohibited.",
        )
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)
    if not (has_upper and has_lower and has_digit and has_special):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one uppercase letter, one lowercase letter, one digit, and one special character.",
        )


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "OPERATOR"
    full_name: str = ""


class UserUpdate(BaseModel):
    role: Optional[str] = None
    active: Optional[bool] = None
    full_name: Optional[str] = None
    password: Optional[str] = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    full_name: str
    active: bool
    last_login: Optional[datetime] = None
    created_at: Optional[datetime] = None


@router.get("", response_model=List[UserOut])
@router.get("/", response_model=List[UserOut])
def list_users(
    db: Session = Depends(get_db),
    admin_user: dict = Depends(require_permission("manage_users")),
):
    """List all registered system users with active status, roles, and timestamps."""
    return db.query(User).order_by(User.id).all()


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    req: UserCreate,
    db: Session = Depends(get_db),
    admin_user: dict = Depends(require_permission("manage_users")),
):
    """Create a new system user with password complexity enforcement."""
    username = req.username.strip().lower()
    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username cannot be empty.")

    # Validate role
    role = req.role.upper().strip()
    if role not in ROLE_HIERARCHY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{role}'. Allowed roles: {list(ROLE_HIERARCHY.keys())}",
        )

    # Check uniqueness
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists.")

    # Validate password complexity
    validate_password_complexity(req.password)

    new_user = User(
        username=username,
        password_hash=hash_password(req.password),
        role=role,
        full_name=req.full_name.strip(),
        active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    log_action(
        db=db,
        actor=admin_user.get("sub", "admin"),
        actor_role=admin_user.get("role", "ADMIN"),
        action="CREATE_USER",
        target_type="user",
        target_id=str(new_user.id),
        details={"username": username, "role": role},
    )

    return new_user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    req: UserUpdate,
    db: Session = Depends(get_db),
    admin_user: dict = Depends(require_permission("manage_users")),
):
    """
    Update user account attributes (role, active state, full name, password).
    Strictly prevents demoting or deactivating the last active administrator.
    """
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    # Guard: Last admin protection
    if target_user.role == "ADMIN":
        active_admins = db.query(User).filter(User.role == "ADMIN", User.active == True).count()
        is_deactivating = req.active is False
        is_demoting = req.role is not None and req.role.upper() != "ADMIN"
        if (is_deactivating or is_demoting) and active_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate or demote the last active system administrator.",
            )

    # Update role
    if req.role is not None:
        role = req.role.upper().strip()
        if role not in ROLE_HIERARCHY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role '{role}'. Allowed roles: {list(ROLE_HIERARCHY.keys())}",
            )
        target_user.role = role

    # Update active status
    if req.active is not None:
        target_user.active = req.active

    # Update full name
    if req.full_name is not None:
        target_user.full_name = req.full_name.strip()

    # Update password if provided
    if req.password is not None:
        validate_password_complexity(req.password)
        target_user.password_hash = hash_password(req.password)

    db.commit()
    db.refresh(target_user)

    log_action(
        db=db,
        actor=admin_user.get("sub", "admin"),
        actor_role=admin_user.get("role", "ADMIN"),
        action="UPDATE_USER",
        target_type="user",
        target_id=str(target_user.id),
        details={"username": target_user.username, "active": target_user.active, "role": target_user.role},
    )

    return target_user


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin_user: dict = Depends(require_permission("manage_users")),
):
    """
    Soft-delete / deactivate a system user account.
    Strictly prevents deleting or deactivating the last active administrator.
    """
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    # Guard: Last admin protection
    if target_user.role == "ADMIN":
        active_admins = db.query(User).filter(User.role == "ADMIN", User.active == True).count()
        if active_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete or deactivate the last active system administrator.",
            )

    target_user.active = False
    db.commit()

    log_action(
        db=db,
        actor=admin_user.get("sub", "admin"),
        actor_role=admin_user.get("role", "ADMIN"),
        action="DEACTIVATE_USER",
        target_type="user",
        target_id=str(target_user.id),
        details={"username": target_user.username},
    )

    return {"deleted": True, "user_id": user_id, "username": target_user.username, "active": False}

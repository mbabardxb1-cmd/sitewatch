from sqlalchemy.orm import Session

from app.models import AuditLog


def record(db: Session, actor_id, action: str, target_type: str | None = None, target_id=None,
           ip: str | None = None, **detail) -> None:
    """Append a security audit-trail entry. Caller commits."""
    db.add(AuditLog(
        actor_id=actor_id, action=action, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None, ip=ip, detail=detail,
    ))

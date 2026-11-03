"""Permission catalogue and default role grants.

This module is the single source of truth. `sync_roles_and_permissions` makes the
database match it (run by the migration and by `python -m app.cli sync-permissions`).
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Permission, Role

SUPER_ADMIN = "super_admin"
DEVELOPER = "developer"
CLIENT = "client"

PERMISSIONS: dict[str, str] = {
    # Administration
    "user.manage": "Create, edit, deactivate users and assign roles",
    "client.manage": "Create and edit client organizations",
    "client.read": "List client organizations",
    "settings.manage": "Change system settings, schedules and report templates",
    "integration.manage": "Manage hosting/registrar API integrations",
    "notification.manage": "Manage notification rules and recipients",
    "notification.read": "View notification rules and delivery logs",
    "website.all": "Access every website regardless of assignment",
    # Websites
    "website.read": "View assigned websites",
    "website.write": "Create, edit and archive websites",
    "website.assign": "Assign users to websites",
    "website.crawl_config": "Change crawl configuration",
    # Monitoring
    "uptime.read_detailed": "View raw checks, HTTP codes, error details and logs",
    "uptime.check_now": "Run a manual uptime check",
    "incident.read": "View incidents",
    "incident.manage": "Acknowledge incidents",
    # Audits & issues
    "audit.read_detailed": "View full audit findings",
    "audit.run": "Trigger a manual audit",
    "audit.export": "Export audit results",
    "issue.read_summary": "View summarized issues",
    "issue.read_detailed": "View full issue details",
    "issue.manage": "Change issue status and assignee",
    "issue.note.read": "Read internal notes",
    "issue.note.write": "Write internal notes",
    # Expiry
    "expiry.read": "View domain/hosting/SSL status (client users also need the client flag)",
    "expiry.read_detailed": "View registrar/hosting details and internal notes",
    "expiry.write": "Enter hosting renewal data and refresh lookups",
    # Reports
    "report.read": "View and download approved client reports",
    "report.read_all": "View and download all reports including technical ones",
    "report.generate": "Generate reports manually",
    "report.approve": "Approve reports for client access and email them",
    "maintenance.write": "Record maintenance work performed",
}

ROLES: dict[str, tuple[str, set[str]]] = {
    SUPER_ADMIN: ("Super Administrator", set(PERMISSIONS)),
    DEVELOPER: (
        "Developer / Technical Team",
        {
            "client.read", "notification.read",
            "website.read", "website.write", "website.crawl_config",
            "uptime.read_detailed", "uptime.check_now", "incident.read", "incident.manage",
            "audit.read_detailed", "audit.run", "audit.export",
            "issue.read_summary", "issue.read_detailed", "issue.manage", "issue.note.read", "issue.note.write",
            "expiry.read", "expiry.read_detailed", "expiry.write",
            "report.read", "report.read_all", "report.generate", "maintenance.write",
        },
    ),
    CLIENT: (
        "Client / Read-only",
        {"website.read", "incident.read", "issue.read_summary", "expiry.read", "report.read"},
    ),
}


def sync_roles_and_permissions(db: Session) -> None:
    existing_perms = {p.key: p for p in db.scalars(select(Permission))}
    for key, desc in PERMISSIONS.items():
        if key in existing_perms:
            existing_perms[key].description = desc
        else:
            existing_perms[key] = Permission(key=key, description=desc)
            db.add(existing_perms[key])
    db.flush()
    for role_key, (name, grants) in ROLES.items():
        role = db.scalar(select(Role).where(Role.key == role_key))
        if role is None:
            role = Role(key=role_key, name=name)
            db.add(role)
        role.name = name
        role.permissions = [existing_perms[k] for k in sorted(grants)]
    db.flush()

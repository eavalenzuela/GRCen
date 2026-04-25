from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"
    AUDITOR = "auditor"


class Permission(str, Enum):
    VIEW = "view"
    VIEW_GRAPH = "view_graph"
    VIEW_PII = "view_pii"  # See fields marked sensitive=True on their asset types
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    IMPORT = "import"
    EXPORT = "export"
    MANAGE_ALERTS = "manage_alerts"
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT = "view_audit"
    APPROVE = "approve"  # Approve or reject pending workflow changes
    MANAGE_ORGS = "manage_orgs"  # Cross-org admin: create/delete orgs, view all tenants


# MANAGE_ORGS is NOT granted by any role — only the is_superadmin user flag
# unlocks it. Keeping it out of every role keeps a per-org admin from quietly
# spawning new tenants.
_NON_ROLE_PERMISSIONS = {Permission.MANAGE_ORGS}

ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.ADMIN: set(Permission) - _NON_ROLE_PERMISSIONS,
    UserRole.EDITOR: {
        Permission.VIEW,
        Permission.VIEW_GRAPH,
        Permission.VIEW_PII,
        Permission.CREATE,
        Permission.EDIT,
        Permission.DELETE,
        Permission.IMPORT,
        Permission.EXPORT,
        Permission.MANAGE_ALERTS,
    },
    UserRole.VIEWER: {
        Permission.VIEW,
        Permission.VIEW_GRAPH,
    },
    UserRole.AUDITOR: {
        Permission.VIEW,
        Permission.VIEW_GRAPH,
        Permission.VIEW_PII,
        Permission.EXPORT,
        Permission.VIEW_AUDIT,
    },
}


def has_permission(role: UserRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())

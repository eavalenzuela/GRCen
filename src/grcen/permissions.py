from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"
    AUDITOR = "auditor"


class Permission(str, Enum):
    VIEW = "view"
    VIEW_GRAPH = "view_graph"
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    IMPORT = "import"
    EXPORT = "export"
    MANAGE_ALERTS = "manage_alerts"
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT = "view_audit"


ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.ADMIN: set(Permission),
    UserRole.EDITOR: {
        Permission.VIEW,
        Permission.VIEW_GRAPH,
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
        Permission.EXPORT,
        Permission.VIEW_AUDIT,
    },
}


def has_permission(role: UserRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())

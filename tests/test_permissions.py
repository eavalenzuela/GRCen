"""Unit tests for the permissions module."""

from grcen.permissions import ROLE_PERMISSIONS, Permission, UserRole, has_permission


def test_admin_has_all_permissions():
    for perm in Permission:
        assert has_permission(UserRole.ADMIN, perm), f"Admin missing {perm}"


def test_editor_permissions():
    allowed = {
        Permission.VIEW, Permission.VIEW_GRAPH, Permission.CREATE,
        Permission.EDIT, Permission.DELETE, Permission.IMPORT,
        Permission.EXPORT, Permission.MANAGE_ALERTS,
    }
    denied = {Permission.MANAGE_USERS}

    for perm in allowed:
        assert has_permission(UserRole.EDITOR, perm), f"Editor should have {perm}"
    for perm in denied:
        assert not has_permission(UserRole.EDITOR, perm), f"Editor should not have {perm}"


def test_viewer_permissions():
    allowed = {Permission.VIEW, Permission.VIEW_GRAPH}
    denied = set(Permission) - allowed

    for perm in allowed:
        assert has_permission(UserRole.VIEWER, perm), f"Viewer should have {perm}"
    for perm in denied:
        assert not has_permission(UserRole.VIEWER, perm), f"Viewer should not have {perm}"


def test_auditor_permissions():
    allowed = {Permission.VIEW, Permission.VIEW_GRAPH, Permission.EXPORT, Permission.VIEW_AUDIT}
    denied = set(Permission) - allowed

    for perm in allowed:
        assert has_permission(UserRole.AUDITOR, perm), f"Auditor should have {perm}"
    for perm in denied:
        assert not has_permission(UserRole.AUDITOR, perm), f"Auditor should not have {perm}"


def test_all_roles_have_permission_entries():
    for role in UserRole:
        assert role in ROLE_PERMISSIONS, f"Missing ROLE_PERMISSIONS entry for {role}"

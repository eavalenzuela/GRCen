"""Unit tests for the permissions module."""

from grcen.permissions import ROLE_PERMISSIONS, Permission, UserRole, has_permission


def test_admin_has_all_per_org_permissions():
    """Admin holds every permission EXCEPT MANAGE_ORGS, which is superadmin-only."""
    for perm in Permission:
        if perm is Permission.MANAGE_ORGS:
            continue
        assert has_permission(UserRole.ADMIN, perm), f"Admin missing {perm}"


def test_admin_does_not_have_manage_orgs():
    """Per-org admins must not get cross-org powers automatically."""
    assert not has_permission(UserRole.ADMIN, Permission.MANAGE_ORGS)


def test_editor_permissions():
    allowed = {
        Permission.VIEW, Permission.VIEW_GRAPH, Permission.VIEW_PII,
        Permission.CREATE, Permission.EDIT, Permission.DELETE,
        Permission.IMPORT, Permission.EXPORT, Permission.MANAGE_ALERTS,
    }
    denied = {Permission.MANAGE_USERS, Permission.VIEW_AUDIT}

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
    allowed = {
        Permission.VIEW, Permission.VIEW_GRAPH, Permission.VIEW_PII,
        Permission.EXPORT, Permission.VIEW_AUDIT,
    }
    denied = set(Permission) - allowed

    for perm in allowed:
        assert has_permission(UserRole.AUDITOR, perm), f"Auditor should have {perm}"
    for perm in denied:
        assert not has_permission(UserRole.AUDITOR, perm), f"Auditor should not have {perm}"


def test_all_roles_have_permission_entries():
    for role in UserRole:
        assert role in ROLE_PERMISSIONS, f"Missing ROLE_PERMISSIONS entry for {role}"

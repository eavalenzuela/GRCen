"""Encryption scope and compliance-profile definitions.

A *scope* is a named category of sensitive data together with the
database columns (or files) it covers.  A *profile* is a named bundle
of scopes that maps to a real-world compliance posture so that admins
can say "enable GDPR" rather than toggling individual scopes.

Adding a new integration (e.g. SAML) only requires appending
:class:`FieldTarget` entries to the relevant scope — no new profiles
or configuration changes are needed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldTarget:
    """A single (table, column) pair that a scope covers.

    For key-value tables like ``oidc_config`` where only certain rows
    contain secrets, set *filter_key_column* and *filter_key_values*
    so that migrations and the admin UI know which rows to touch.
    """

    table: str
    column: str
    filter_key_column: str | None = None
    filter_key_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class EncryptionScope:
    name: str
    display_name: str
    description: str
    targets: tuple[FieldTarget, ...] = ()
    # Warn in the UI when this scope has significant side-effects.
    warning: str | None = None


@dataclass(frozen=True)
class EncryptionProfile:
    name: str
    display_name: str
    description: str
    scope_names: tuple[str, ...] = ()


# ── built-in scopes ──────────────────────────────────────────────────────

SCOPE_SSO_SECRETS = EncryptionScope(
    name="sso_secrets",
    display_name="SSO Provider Secrets",
    description="OIDC/SAML client secrets and private keys",
    targets=(
        FieldTarget(
            table="oidc_config",
            column="value",
            filter_key_column="key",
            filter_key_values=("client_secret",),
        ),
        FieldTarget(
            table="saml_config",
            column="value",
            filter_key_column="key",
            filter_key_values=("sp_private_key",),
        ),
    ),
)

SCOPE_SMTP_SECRETS = EncryptionScope(
    name="smtp_secrets",
    display_name="SMTP Password",
    description="SMTP password for outbound email",
    targets=(
        FieldTarget(
            table="smtp_config",
            column="value",
            filter_key_column="key",
            filter_key_values=("password",),
        ),
    ),
)

SCOPE_WEBHOOK_SECRETS = EncryptionScope(
    name="webhook_secrets",
    display_name="Webhook Signing Secrets",
    description="HMAC secrets used to sign outgoing webhook payloads",
    targets=(
        FieldTarget(table="webhooks", column="secret"),
    ),
)

SCOPE_USER_PII = EncryptionScope(
    name="user_pii",
    display_name="User PII",
    description="Email addresses and identifying information",
    targets=(
        FieldTarget(table="users", column="email"),
    ),
)

SCOPE_SESSION_PII = EncryptionScope(
    name="session_pii",
    display_name="Session Metadata",
    description="IP addresses in session records",
    targets=(
        FieldTarget(table="sessions", column="ip_address"),
    ),
)

SCOPE_AUDIT_PII = EncryptionScope(
    name="audit_pii",
    display_name="Audit Log PII",
    description="PII captured in audit log change snapshots",
    targets=(
        FieldTarget(table="audit_log", column="changes"),
    ),
    warning="Existing audit entries are not retroactively encrypted.",
)

SCOPE_ASSET_METADATA = EncryptionScope(
    name="asset_metadata",
    display_name="Asset Custom Fields",
    description="JSONB metadata on assets (may contain sensitive custom fields)",
    targets=(
        FieldTarget(table="assets", column="metadata"),
    ),
    warning="Encrypted metadata cannot be searched with PostgreSQL JSON operators.",
)

SCOPE_TOTP_SECRETS = EncryptionScope(
    name="totp_secrets",
    display_name="TOTP secrets",
    description="Per-user TOTP shared secrets in user_totp.secret",
    targets=(
        FieldTarget(table="user_totp", column="secret"),
    ),
    warning="Existing TOTP rows are not retroactively encrypted; they re-encrypt on next confirm/disable.",
)

SCOPE_FILE_CONTENTS = EncryptionScope(
    name="file_contents",
    display_name="Uploaded Files",
    description="Encrypt file contents on disk",
    targets=(),  # handled by the upload/download path, not by column migration
    warning="Existing files must be re-encrypted after enabling.",
)

ALL_SCOPES: dict[str, EncryptionScope] = {
    s.name: s
    for s in [
        SCOPE_SSO_SECRETS,
        SCOPE_SMTP_SECRETS,
        SCOPE_WEBHOOK_SECRETS,
        SCOPE_USER_PII,
        SCOPE_SESSION_PII,
        SCOPE_AUDIT_PII,
        SCOPE_ASSET_METADATA,
        SCOPE_TOTP_SECRETS,
        SCOPE_FILE_CONTENTS,
    ]
}

# ── built-in profiles ────────────────────────────────────────────────────

PROFILE_MINIMAL = EncryptionProfile(
    name="minimal",
    display_name="Minimal (Secrets Only)",
    description="Encrypts SSO, SMTP, and webhook secrets.  No PII encryption.",
    scope_names=("sso_secrets", "smtp_secrets", "webhook_secrets"),
)

PROFILE_GDPR = EncryptionProfile(
    name="gdpr",
    display_name="GDPR",
    description=(
        "Encrypts all personal data: emails, IP addresses, audit snapshots.  "
        "Satisfies GDPR Art.\u00a032(1)(a) pseudonymisation and encryption."
    ),
    scope_names=(
        "sso_secrets",
        "smtp_secrets",
        "webhook_secrets",
        "user_pii",
        "session_pii",
        "audit_pii",
    ),
)

PROFILE_FULL = EncryptionProfile(
    name="full",
    display_name="Full Encryption",
    description="Encrypts all supported fields and uploaded files.",
    scope_names=(
        "sso_secrets",
        "smtp_secrets",
        "webhook_secrets",
        "user_pii",
        "session_pii",
        "audit_pii",
        "asset_metadata",
        "totp_secrets",
        "file_contents",
    ),
)

PROFILE_CUSTOM = EncryptionProfile(
    name="custom",
    display_name="Custom",
    description="Select individual scopes below.",
    scope_names=(),  # populated from the database at runtime
)

ALL_PROFILES: dict[str, EncryptionProfile] = {
    p.name: p
    for p in [PROFILE_MINIMAL, PROFILE_GDPR, PROFILE_FULL, PROFILE_CUSTOM]
}

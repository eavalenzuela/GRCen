"""Database-backed SAML 2.0 configuration with in-memory cache."""

from dataclasses import dataclass

import asyncpg

from grcen.services import encryption_config
from grcen.services.encryption import decrypt_field, encrypt_field

_DEFAULTS = {
    "idp_entity_id": "",
    "idp_sso_url": "",
    "idp_slo_url": "",
    "idp_x509_cert": "",
    "sp_entity_id": "",
    "sp_private_key": "",
    "sp_x509_cert": "",
    "name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    "role_attribute": "Role",
    "role_mapping": "{}",
    "default_role": "viewer",
    "display_name": "SAML SSO",
    "want_assertions_signed": "true",
    "want_name_id_encrypted": "false",
}

# Keys whose values should be encrypted under the sso_secrets scope.
_SECRET_KEYS = frozenset({"sp_private_key"})


@dataclass
class SAMLSettings:
    idp_entity_id: str = ""
    idp_sso_url: str = ""
    idp_slo_url: str = ""
    idp_x509_cert: str = ""
    sp_entity_id: str = ""
    sp_private_key: str = ""
    sp_x509_cert: str = ""
    name_id_format: str = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
    role_attribute: str = "Role"
    role_mapping: str = "{}"
    default_role: str = "viewer"
    display_name: str = "SAML SSO"
    want_assertions_signed: str = "true"
    want_name_id_encrypted: str = "false"

    @property
    def enabled(self) -> bool:
        return bool(
            self.idp_entity_id and self.idp_sso_url and self.idp_x509_cert
        )

    @property
    def assertions_signed(self) -> bool:
        return self.want_assertions_signed.lower() == "true"

    @property
    def name_id_encrypted(self) -> bool:
        return self.want_name_id_encrypted.lower() == "true"


# Module-level cache
_cache: SAMLSettings | None = None


async def _load(pool: asyncpg.Pool) -> SAMLSettings:
    global _cache
    rows = await pool.fetch("SELECT key, value FROM saml_config")
    values = {r["key"]: r["value"] for r in rows}

    # Decrypt secret keys if the sso_secrets scope is active.
    scopes = await encryption_config.get_active_scopes(pool)
    if "sso_secrets" in scopes:
        for sk in _SECRET_KEYS:
            if sk in values and values[sk]:
                values[sk] = decrypt_field(values[sk], "sso_secrets")

    _cache = SAMLSettings(
        **{k: values.get(k, v) for k, v in _DEFAULTS.items()}
    )
    return _cache


async def get_settings(pool: asyncpg.Pool) -> SAMLSettings:
    if _cache is None:
        return await _load(pool)
    return _cache


async def reload(pool: asyncpg.Pool) -> SAMLSettings:
    return await _load(pool)


async def update_settings(pool: asyncpg.Pool, **kwargs: str) -> SAMLSettings:
    scopes = await encryption_config.get_active_scopes(pool)
    for key, value in kwargs.items():
        if key in _DEFAULTS:
            store_value = value
            if key in _SECRET_KEYS and "sso_secrets" in scopes and value:
                store_value = encrypt_field(value, "sso_secrets")
            await pool.execute(
                """INSERT INTO saml_config (key, value, updated_at)
                   VALUES ($1, $2, now())
                   ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()""",
                key,
                store_value,
            )
    return await _load(pool)

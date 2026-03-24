# Configuring Encryption at Rest

GRCen supports optional application-level encryption of sensitive database fields and uploaded files. Encryption is:

- **Optional** -- the application works identically with or without it. All encrypt/decrypt calls are no-ops when no key is configured.
- **Granular** -- you choose which categories of data to encrypt via scopes and profiles.
- **BYO key** -- you provide the encryption key as an environment variable. GRCen never generates or stores the master key.

---

## How It Works

GRCen uses **AES-256-GCM** (authenticated encryption) with **HKDF-SHA256** key derivation. A single master key is stretched into per-scope subkeys so that each category of data is cryptographically isolated.

**Ciphertext format:** `enc:1:<base64url(nonce[12] || ciphertext || tag[16])>`

The `enc:1:` prefix allows GRCen to distinguish encrypted values from plaintext, which enables gradual migration -- existing unencrypted data continues to work and is encrypted on next write.

**Blind indexes:** For fields that need equality lookups (e.g. email), GRCen maintains an HMAC-SHA256 blind index alongside the encrypted value. This allows `WHERE email_blind_idx = ?` queries without decrypting every row.

---

## Quick Start

### Step 1: Generate an Encryption Key

```bash
grcen generate-key
```

This prints a base64url-encoded 32-byte random key:

```
ENCRYPTION_KEY=dGhpcyBpcyBhIHNhbXBsZSBrZXkgZm9yIGRvY3M...
```

**Store this key securely.** If you lose it, encrypted data cannot be recovered.

### Step 2: Set the Environment Variable

Add to your `.env` file:

```bash
ENCRYPTION_KEY=<the key from step 1>
```

Or in `docker-compose.yml`:

```yaml
services:
  app:
    environment:
      ENCRYPTION_KEY: "${ENCRYPTION_KEY:?Set ENCRYPTION_KEY in .env}"
```

### Step 3: Restart and Select a Profile

Restart the application, log in as an admin, and go to **Admin > Encryption Settings**. Select a profile and click **Apply Changes**.

Existing data is migrated in the background -- plaintext values are encrypted on next read/write cycle.

---

## Encryption Profiles

Profiles are pre-configured bundles of scopes that match common compliance requirements. Select one from the admin UI, or use **Custom** to pick individual scopes.

| Profile | Scopes Included | Use Case |
|---|---|---|
| **Minimal** | SSO Secrets | Encrypt only IdP client secrets and private keys |
| **GDPR** | SSO Secrets, User PII, Session Metadata, Audit Log PII | Satisfies GDPR Art. 32(1)(a) pseudonymisation and encryption |
| **Full** | All 6 scopes | Maximum protection -- encrypts everything supported |
| **Custom** | You choose | Select individual scopes for your specific requirements |
| **Disabled** | None | Encryption key is set but no scopes are active |

---

## Encryption Scopes

Each scope covers a specific category of sensitive data:

| Scope | What It Encrypts | Notes |
|---|---|---|
| **SSO Provider Secrets** | OIDC client secret, SAML SP private key | Targets specific rows in the `oidc_config` and `saml_config` key-value tables |
| **User PII** | Email addresses in the `users` table | A blind index is maintained for email lookups |
| **Session Metadata** | IP addresses in the `sessions` table | |
| **Audit Log PII** | Change snapshots in the `audit_log` table | Existing audit entries are not retroactively encrypted |
| **Asset Custom Fields** | JSONB metadata on assets | Encrypted metadata cannot be searched with PostgreSQL JSON operators |
| **Uploaded Files** | File contents on disk | Existing files must be re-encrypted after enabling (see Key Rotation) |

---

## Key Management

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ENCRYPTION_KEY` | Yes (to enable encryption) | Base64url-encoded 32-byte key. The active encryption key. |
| `ENCRYPTION_KEY_RETIRED` | Only during rotation | The previous key, used for decryption only during key rotation. |

### Generating a Key

```bash
# Using the built-in command
grcen generate-key

# Or manually with OpenSSL
python3 -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

### Key Storage Recommendations

- **Docker/Compose:** Use Docker secrets or a `.env` file that is not checked into version control.
- **Kubernetes:** Use a Secret resource mounted as an environment variable.
- **Cloud:** Use your provider's secret manager (AWS Secrets Manager, GCP Secret Manager, Azure Key Vault) to inject the key at runtime.
- **Bare metal:** Use an encrypted file or hardware security module (HSM) to store the key.

Never commit the encryption key to source control. The `.env` file is gitignored by default.

---

## Key Rotation

Key rotation re-encrypts all active scopes with a new key. The process uses a two-key system so there is no downtime -- the application can decrypt data encrypted with either key during the transition.

### Step 1: Generate a New Key

```bash
grcen generate-key
```

### Step 2: Configure Both Keys

Set `ENCRYPTION_KEY` to the **new** key and `ENCRYPTION_KEY_RETIRED` to the **old** key:

```bash
ENCRYPTION_KEY=<new key>
ENCRYPTION_KEY_RETIRED=<old key>
```

### Step 3: Restart the Application

```bash
docker compose restart app
# or: systemctl restart grcen
```

At this point, the application decrypts with either key but encrypts all new writes with the new key.

### Step 4: Re-encrypt Existing Data

```bash
# Docker
docker compose exec app grcen rotate-keys

# Local
grcen rotate-keys
```

This reads every encrypted value across all active scopes, decrypts it (using whichever key works), and re-encrypts it with the new key. Output shows progress per scope:

```
  sso_secrets: 2 value(s) re-encrypted.
  user_pii: 15 value(s) re-encrypted.
  session_pii: 42 value(s) re-encrypted.
Done. 59 total value(s) rotated.
You may now remove ENCRYPTION_KEY_RETIRED from your environment.
```

### Step 5: Remove the Old Key

Once rotation is complete, remove `ENCRYPTION_KEY_RETIRED` and restart:

```bash
# Remove from .env
ENCRYPTION_KEY=<new key>
# ENCRYPTION_KEY_RETIRED is no longer needed

docker compose restart app
```

---

## Enabling Encryption on an Existing Deployment

If you have an existing GRCen deployment with unencrypted data:

1. Generate a key and set `ENCRYPTION_KEY`
2. Restart the application
3. Select a profile from **Admin > Encryption Settings** and click **Apply Changes**
4. Existing plaintext data will be encrypted gradually as it is read and written back

The `enc:1:` prefix on ciphertext allows mixed-state operation -- `decrypt_field()` returns plaintext values unchanged if they don't have the prefix. This means you don't need to run a one-time migration; data encrypts naturally over time.

For immediate full migration, run `grcen rotate-keys` after enabling scopes. Despite the name, this command also handles initial encryption of plaintext data.

---

## Disabling Encryption

To disable encryption for specific scopes:

1. Go to **Admin > Encryption Settings**
2. Switch to **Custom** profile and uncheck the scopes you want to disable, or select **Disabled**
3. Click **Apply Changes**

Data that was previously encrypted remains encrypted in the database. It will be decrypted transparently on read as long as `ENCRYPTION_KEY` is still set. To permanently decrypt all data, keep the key set, disable all scopes, and run `grcen rotate-keys` -- this decrypts everything back to plaintext.

**Do not remove `ENCRYPTION_KEY` while encrypted data exists.** If the key is removed, encrypted values will be returned as-is (with the `enc:1:` prefix), which will cause errors in the application.

---

## Troubleshooting

**"No encryption key configured" in the admin UI**
- Verify that `ENCRYPTION_KEY` is set in the environment and the application was restarted after setting it.
- Check that the key is a valid base64url-encoded string of exactly 32 bytes (44 characters with padding).

**Data appears as `enc:1:...` in the database**
- This is expected. The `enc:1:` prefix indicates encrypted ciphertext. It is decrypted transparently by the application.
- If you see this in API responses, the `ENCRYPTION_KEY` may not be set or may be incorrect.

**"InvalidTag" or decryption errors**
- The encryption key does not match the key used to encrypt the data. If you rotated keys, make sure `ENCRYPTION_KEY_RETIRED` is set to the old key.
- If you lost the key, the encrypted data cannot be recovered.

**Encrypted email lookups are slow**
- Email lookups use a blind index (`email_blind_idx`). If this column is missing its index, run: `CREATE INDEX IF NOT EXISTS idx_users_email_blind ON users (email_blind_idx);`

**Cannot search asset metadata after enabling encryption**
- This is expected. Encrypted JSONB fields cannot be queried with PostgreSQL JSON operators (`->`, `->>`, `@>`). Consider whether you need the **Asset Custom Fields** scope or if other scopes meet your requirements.

**File uploads/downloads fail after enabling file encryption**
- Existing files uploaded before encryption was enabled are stored as plaintext. The application handles both transparently.
- If new uploads fail, check that the `UPLOAD_DIR` is writable and that the encryption key is set.

---

## Architecture

```
                 ENCRYPTION_KEY (env var)
                        |
                  ┌─────┴─────┐
                  │  HKDF-256 │  (per-scope key derivation)
                  └─────┬─────┘
           ┌────────────┼────────────┐
           v            v            v
     scope: sso    scope: pii   scope: files
           |            |            |
      AES-256-GCM  AES-256-GCM  AES-256-GCM
           |            |            |
     ┌─────┴───┐   ┌────┴────┐   ┌──┴───┐
     │oidc_cfg │   │ users   │   │ disk │
     │saml_cfg │   │sessions │   │      │
     └─────────┘   │audit_log│   └──────┘
                   │ assets  │
                   └─────────┘
```

Each scope derives its own subkey from the master key via HKDF with the scope name as the info parameter. This means compromising one scope's subkey does not compromise data in other scopes.

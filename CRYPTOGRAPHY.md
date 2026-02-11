# Cryptography Overview

This app protects patient data using:
- SQLCipher for database-at-rest encryption
- Envelope encryption for documents stored on disk

## Threat model (high level)
- If an attacker steals the vault files, they should not be able to read patient data without the user's password or recovery key.
- If a vault is corrupted or keys do not match, the app should fail closed (no partial access).

## Components

### Database Master Key (DMK)
- A 32-byte random key created on first run.
- Used to unlock the SQLCipher database.
- Never stored in plaintext on disk.

### Keybag (`medical_records_v1.db.keybag`)
A JSON sidecar file storing the DMK wrapped (encrypted) under two methods:
1) Password wrap: key derived from user password via PBKDF2-HMAC-SHA256 + per-vault salt
2) Recovery wrap: key derived from a randomly generated recovery key string

Keybag contents are not plaintext secrets: it contains salts, KDF params, and encrypted DMK blobs.
The recovery key string itself must be treated as a secret.

### File Master Key (FMK)
- A Fernet key used to encrypt document bytes on disk (`*.enc`).
- Stored in the DB in `app_settings` as `crypto.fmk_wrapped_by_dmk_b64`.
- The FMK is wrapped using a Fernet key derived from the DMK (DMK -> base64 -> Fernet key).

## Document encryption flow
- On upload:
  - Load/create FMK (unwrapped using DMK)
  - Encrypt file bytes with FMK (Fernet)
  - Write ciphertext to disk as `*.enc`
- On open:
  - Load FMK
  - Decrypt `*.enc` to a temporary PDF for viewing

## Recovery key UX
- On first vault creation, the app generates a recovery key and presents it to the user.
- Recovery key rotation requires current password verification and generates a new key.

## Startup crypto self-test
On login, the app runs a self-test that:
- Confirms keybag exists
- (If password provided) confirms password unwraps the same DMK as the current session
- Loads/creates FMK via DMK and performs a round-trip encrypt/decrypt test
- Optionally attempts to decrypt one existing `.enc` doc (lightweight)

## Notes
- Never log plaintext secrets (passwords, recovery keys, DMK bytes).
- Keybag and ciphertext are expected to be stored unencrypted; security relies on strong passwords, KDF, and OS file permissions.
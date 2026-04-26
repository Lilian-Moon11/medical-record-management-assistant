# Security Policy

Medical Record Management Assistant is designed around a strict, local-first zero-trust model to protect sensitive patient health data.

## Supported Versions

Currently, the primary development is focused solely on the `main` branch. This project is provided "as is," and while I welcome open source forks, there is no guaranteed service-level agreement or patch timeline for older forks.

| Version | Supported          |
| ------- | ------------------ |
| MVP     | :white_check_mark: |

## The Threat Model

The security boundary relies completely on local device access. 

- **Local First**: There is no server, no cloud, no API synchronization, and no web-hosted endpoints.
- **SQLCipher Data Encryption**: The primary SQLite database is encrypted at rest using AES-256 via SQLCipher.
- **Crypto v2 Architecture**: A master Data Master Key (DMK) is generated via PBKDF2 securely from the user's password. This DMK envelops File Master Keys (FMK) which are used to encrypt raw PDF binaries on disk, enabling password changes and key rotation without decrypting/re-encrypting every payload.

## Reporting a Vulnerability

If you discover a vulnerability that compromises this local-first model (such as a flaw in how the UI parses malicious inputs or issues with the recovery key pipeline), please act responsibly.

**Do not open a public GitHub issue.** Doing so can expose the flaw to malicious actors and threaten the data integrity of users running local clones.

Instead, please email: `[Insert Security Contact Email Here]`

Please include:
- A description of the vulnerability.
- Your OS and execution environment.
- Step-by-step instructions to reproduce it.

Thank you for helping protect patient data.

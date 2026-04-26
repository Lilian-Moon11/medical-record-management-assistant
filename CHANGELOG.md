# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]
### Added
- Feature requests and tasks detailed in the Open Source Community Roadmap in `README.md`.

## [0.1.0] - MVP Release - The Pipeline Foundation
### Added
- **Core Architecture**: Flet-based cross-platform GUI designed with native UI principles, featuring theming and accessible layouts.
- **SQLCipher Integration**: AES-256 database protection with an enforced Data Master Key (DMK) and File Master Key (FMK) envelope architecture to secure raw PDF/image blobs with recovery-key rotation capability.
- **Health Record Catalog**: Structured, FHIR-lite schemas tracking Patient details, Demographics, Providers, Surgeries, Medications, Conditions, Allergies, Immunizations, and Family History. Includes dedicated UI tables and hereditary risk summaries.
- **Custom Printable Summaries**: Dynamically rendering all patient statistics, active conditions, and historical records into a structured, customized PDF summary that can be exported directly.
- **Local AI Extraction Pipeline**: Implementation of offline Large/Small Language Model logic (utilizing ONNX and local model weights) to automatically extract clinical details and populate medical forms from raw PDFs asynchronously without cloud API dependencies. 
- **Wizard Flow**: A multi-step structured flow to dynamically generate "Release of Information" (ROI) requests based on predefined and user-provided inputs.
- **Dynamic PDF Overlays**: Logic to combine and flatten PyMuPDF generated templates, filling form fields programmatically based on user data. Includes direct in-app signature padding.
- **Airlock Import/Export Pipeline**: Secure, encrypted JSON/file-blob deduplication strategies allowing for true air-gapped backup workflows and Excel-based transfers.
- **Unencrypted Export Engine**: Dedicated pipeline (`unencrypted_export.py`) allowing users to safely "break glass" and decrypt specific documents or export their entire clinical database to plain human-readable formats for external migrations.
- **AI Conflict Inbox**: A human-in-the-loop validation UI that cleanly detects and highlights record discrepancies before committing AI-extracted data.
- **Data Provenance Subsystem**: Patient-controlled toggles to instantly trace the explicit source and chronological update history of any clinical data point.
- **Clinical Trend Visualization**: Custom graphical representations mapping lab numbers across longitudinal historical timeframes.
- **Accessibility & Device Management**: Implementations for High Contrast Theme modes, UI scaling scroll-locks, and safe shared-device full-wipe functionalities.
- **Login Vault Protections**: Implementation of rate-limiting algorithms and rapid-retry guards on the authentication screen to protect against brute-force attacks.
- **Test Suite & Start-Up Diagnostics**: A robust `unittest` idempotency suite alongside a runtime Cryptographic Self-Test (`selftest.py`) that executes before the app UI loads, validating the database integrity to prevent corruption on startup.

### Changed
- Replaced traditional web-based authentication layers with a strictly isolated local keybag protocol to verify password entry.
- Overhauled PyInstaller compilation targets to drastically improve performance of local app initializations.

### Removed
- Removed generic AI conversational chatbot interfaces inside the ingestion pipeline, pivoting strictly to a background agent workflow designed for data extraction, minimizing AI hallucination risk and adhering tightly to healthcare software architecture norms.

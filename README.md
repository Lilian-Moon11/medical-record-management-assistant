# Medical Record Management Assistant

Medical Record Management Assistant is a secure, private desktop app for storing medical records and preparing Release of Information (ROI) forms — without sending your data to the cloud.

Your data stays on your computer, encrypted, and under your control.

Status: MVP (In Development)
License: GNU AGPLv3
Created by: Lilian-Moon11
Issues & Support: https://github.com/Lilian-Moon11/local-patient-advocate/issues


## What This App Does

- Store medical documents (PDFs, records, reports) securely on your computer
- Organize records by patient profile
- Automatically extract clinical information like Conditions or Medications from scanned OCR documents using a completely offline, local AI Pipeline.
- Open documents when you need them, without permanently decrypting them
- Help prepare Release of Information (ROI) paperwork
- Guarantee data portability via an Airlock Import/Export engine to prevent vendor lock-in
- Work totally offline — no internet connection required

This app is designed for people who care deeply about privacy and don’t want their medical information uploaded to third-party services.


## The Privacy Promise (Plain Language)

Medical Record Management Assistant follows a local-first, zero-trust design:

### Runs only on your computer
Nothing is uploaded anywhere. No servers. No accounts.

### Strong encryption protects your data
All records are locked using industry-standard encryption. Without the correct password, the data is unreadable.

### No cloud syncing, ever
Your files never leave your device unless you manually move them.

### Accessible by design
Built with accessibility in mind, including keyboard navigation and screen-reader compatibility.


## Important Security Note (Please Read)

When you first use the app, you create a database password.

This password:

Protects the encryption keys for your records

Is never stored

Cannot be reset by the app or the developer

### What this means

If you lose:

your password and

your recovery key

your data cannot be recovered

This is intentional. It prevents anyone else from accessing sensitive medical information.

The app will guide you through saving a recovery key during setup.
Please store it somewhere safe (for example: a password manager or printed copy).

## How the Security Works (High Level)

You don’t need to understand this to use the app — this is just for transparency.

- Your password unlocks a hidden master key
- That master key unlocks the encrypted database
- Documents are encrypted individually and only decrypted temporarily when opened
- On startup, the app checks that everything is consistent and safe before continuing
- If anything looks wrong, the app stops immediately to protect your data.

## Installation & Setup
### What You Need

A computer with Python 3.12 (Strictly required for local AI / OCR pipelines)

Windows, macOS, or Linux

### Step 1: Download the App
git clone https://github.com/Lilian-Moon11/local-patient-advocate.git
cd local-patient-advocate

### Step 2: (Recommended) Create a Virtual Environment
To prevent dependency errors, explicitly use Python 3.12 when creating your environment.

#### Windows

py -3.12 -m venv .venv
.\.venv\Scripts\activate


#### Mac / Linux

python3.12 -m venv .venv
source .venv/bin/activate

### Step 3: Install Required Packages
pip install -r requirements.txt

## How to Run the App

This is a desktop application, not a website.

python main.py


A window will open asking for a Database Password.

If this is your first time, this password creates your secure vault

If you’ve used the app before, enter your existing password

Follow the on-screen instructions carefully, especially when saving your recovery key.

## Future Enhancements (Community Roadmap)

While this project is not actively managed for feature requests, the following backlog items represent areas where the open-source community could provide immense value:

- **Accessibility Audits**: Comprehensive screen reader testing (NVDA on Windows, VoiceOver on Mac) across complex layout grids and Semantic HTML structure validation for data presentation.
- **Distribution Packages**: Building standalone, portable `.exe` and `.app` bundles to abstract command-line usage away from non-technical users.
- **Project Discoverability**: Building a personal website or companion landing page to detail the application architecture and act as a knowledge base.

## Out of Scope (Intentional Scope Management)

To ensure this MVP didn't succumb to feature-creep and remained tightly scoped on its primary value proposition (local-first data extraction and ROI paperwork prep), the following features were explicitly rejected or placed strictly out of scope:

- **Pinning UI Panels to Overview Tab**: *Decided Against.* Rather than building complex state-management capable of cloning visual UI widgets cross-screen, I instead provided granular PDF Selection tools. This fulfills the core user need (viewing arbitrary combinations of data together) without irreparably cluttering the digital workspace.
- **Hybrid Web Access / Explaining "Portions"**: *Rejected.* I received proposals to allow "partial" web access for specific non-sensitive functions. I rejected this to maintain an absolute `zero-trust`, 100% offline security guarantee. If an open-source contributor requires web metrics, it must remain an explicitly distinct fork.
- **Superbill / Insurance Submission Tracking**: *Rejected.* Adding financial workflows muddies the goal of the application. It belongs strictly in an entirely different financial-management project architecture.
- **AI Medical Coding (ICD/CPT)**: *Put on hold.* While the AI pipeline is capable, patient advocates rarely require deriving their own diagnostic/procedural codes mechanically, meaning the legal liability and complexity significantly outweighed any concrete user story.
- **PDF Form Modifications/Annotations**: *Decided Against.* I deliberately did not recreate standard Adobe Acrobat behaviors (drawing text on flat pages) to keep the repository lightweight and solely focused on programmatic workflow filling.
- **Non-AI "Lite" Editions**: *Mitigated differently.* To handle general AI apprehension from older tech users, I instead prioritized radical transparency about the Small Language Model (SLM) executing absolutely independently of external APIs.

## Design Decisions & Evolution (The Pivots)

1. **UX Pivot: Clinical Universality vs. Novelty.** I originally designed an abstracted "Body-System" map for capturing Family history. User testing demonstrated it was highly confusing. I scrapped the complex architecture completely in favor of flat, standard clinical checkbox forms mapping 1:1 with traditional doctor intake paperwork.
2. **Security Pivot: The Keybag Envelope (Crypto v2).** The MVP originally tied database encryption 1:1 with the user-provided password. This proved fatal for UX: if a user compromised their password, rotating it meant I'd have to aggressively load and re-encrypt gigabytes of appended PDF binaries. My pivot implemented a staggered Master Key envelope, allowing instantaneous password and recovery key swaps.
3. **AI Architecture Pivot: Generative vs. Extractive.** I initially experimented with conversational "RAG" (Retrieval-Augmented Generation) chatbots. I quickly realized conversational AI in medical tooling introduces unacceptable hallucination risks for patient advocates. I pivoted hard: I ripped out the chatbot UI and rebuilt the AI pipeline strictly as a background data-extraction agent, mapping clinical data into fixed forms under strict human-in-the-loop supervision.
4. **International Data Architecture.** During my schema mapping for Demographics, I explicitly omitted static dependencies on US Social Security Numbers. However, I preserved generalized abstract Insurance blocks, acknowledging the existence of universally managed care networks internationally.

## Technology (For Transparency)

**The Local AI Component**
To guarantee absolute privacy while modernizing the user experience, this application leverages a highly specialized, localized AI framework. 
- **The Engine**: Execute completely offline using `llama-cpp-python`, allowing the CPU-efficient processing of GGUF quantized models without requiring network connectivity.
- **The Process**: By prioritizing an Extractive agent over a Generative chatbot, the model strictly isolates clinical data points (dates, dosages, conditions) from unstructured PDFs and maps them securely to local schemas.
- **Opt-In Architecture**: To keep the initial footprint lightweight, the ~2.5 GB model is not bundled by default. It is dynamically fetched on the user's explicit first request, meaning skeptical users can comfortably utilize the entire repository without ever invoking the AI module.

**Core Stack**
User Interface: Flet (accessible, native desktop UI)

Database: SQLite encrypted with SQLCipher (AES-256)

Encryption: PBKDF2 key derivation + modern symmetric encryption

Storage: Fully local, encrypted at rest

## License

This project is licensed under the GNU Affero General Public License v3 (AGPLv3).
See the LICENSE file for details.
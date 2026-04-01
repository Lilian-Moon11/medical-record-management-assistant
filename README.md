Local Patient Advocate

Local Patient Advocate is a secure, private desktop app for storing medical records and preparing Release of Information (ROI) forms — without sending your data to the cloud.

Your data stays on your computer, encrypted, and under your control.

Status: MVP (In Development)
License: GNU AGPLv3
Created by: Lilian-Moon11
Issues & Support: https://github.com/Lilian-Moon11/local-patient-advocate/issues


What This App Does

- Store medical documents (PDFs, records, reports) securely on your computer
- Organize records by patient profile
- Automatically extract clinical information like Conditions or Medications from scanned OCR documents using a completely offline, local AI Pipeline.
- Chat with your documents directly via the dashboard using a Local RAG AI Assistant.
- Open documents when you need them, without permanently decrypting them
- Help prepare Release of Information (ROI) paperwork
- Work totally offline — no internet connection required

This app is designed for people who care deeply about privacy and don’t want their medical information uploaded to third-party services.


The Privacy Promise (Plain Language)

Local Patient Advocate follows a local-first, zero-trust design:

Runs only on your computer
Nothing is uploaded anywhere. No servers. No accounts.

Strong encryption protects your data
All records are locked using industry-standard encryption. Without the correct password, the data is unreadable.

No cloud syncing, ever
Your files never leave your device unless you manually move them.

Accessible by design
Built with accessibility in mind, including keyboard navigation and screen-reader compatibility.


Important Security Note (Please Read)

When you first use the app, you create a database password.

This password:

Protects the encryption keys for your records

Is never stored

Cannot be reset by the app or the developer

What this means

If you lose:

your password and

your recovery key

your data cannot be recovered

This is intentional. It prevents anyone else from accessing sensitive medical information.

The app will guide you through saving a recovery key during setup.
Please store it somewhere safe (for example: a password manager or printed copy).

How the Security Works (High Level)

You don’t need to understand this to use the app — this is just for transparency.

Your password unlocks a hidden master key

That master key unlocks the encrypted database

Documents are encrypted individually and only decrypted temporarily when opened

On startup, the app checks that everything is consistent and safe before continuing

If anything looks wrong, the app stops immediately to protect your data.

Installation & Setup
What You Need

A computer with Python 3.12 (Strictly required for local AI / OCR pipelines)

Windows, macOS, or Linux

Step 1: Download the App
git clone https://github.com/Lilian-Moon11/local-patient-advocate.git
cd local-patient-advocate

Step 2: (Recommended) Create a Virtual Environment
To prevent dependency errors, explicitly use Python 3.12 when creating your environment.

Windows

py -3.12 -m venv .venv
.\.venv\Scripts\activate


Mac / Linux

python3.12 -m venv .venv
source .venv/bin/activate

Step 3: Install Required Packages
pip install -r requirements.txt

How to Run the App

This is a desktop application, not a website.

python main.py


A window will open asking for a Database Password.

If this is your first time, this password creates your secure vault

If you’ve used the app before, enter your existing password

Follow the on-screen instructions carefully, especially when saving your recovery key.

Technology (For Transparency)

User Interface: Flet (accessible, native desktop UI)

Database: SQLite encrypted with SQLCipher (AES-256)

Encryption: PBKDF2 key derivation + modern symmetric encryption

Storage: Fully local, encrypted at rest

License

This project is licensed under the GNU Affero General Public License v3 (AGPLv3).
See the LICENSE file for details.
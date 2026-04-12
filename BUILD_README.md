# Medical Record Management Assistant (MRMA) — Developer Build Guide

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | 3.13 tested |
| Windows Developer Mode | Required for SQLCipher wheel symlinks |
| Git | For cloning the repo |

### Windows Developer Mode

SQLCipher's pip wheel creates symlinks during install. Windows blocks this without Developer Mode:

**Settings → System → For developers → Developer Mode → On**

Then install:

```powershell
pip install sqlcipher3-wheels
```

## Setting Up the Environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### llama-cpp-python (no MSVC required)

Install the pre-built binary wheel to avoid needing Visual Studio:

```powershell
pip install llama-cpp-python --prefer-binary
```

## Running in Development

```powershell
python main.py
```

The database is created at:
- **Windows:** `%LOCALAPPDATA%\MRMA\MedicalRecordManagementAssistant\`
- **macOS:** `~/Library/Application Support/MedicalRecordManagementAssistant/`
- **Linux:** `~/.local/share/MedicalRecordManagementAssistant/`

## Building the Portable App (Windows)

```powershell
.\build\build_windows.ps1
```

Output:
- `dist\mrma\mrma.exe` — run-in-place folder (portable)
- `dist\MRMA-portable-win.zip` — distributable archive

**PyInstaller onedir rationale:** `--onedir` is used instead of `--onefile` because `--onefile` extracts the entire bundle to a temp directory on every launch, causing 5–30 second startup delays on older hardware. `--onedir` only loads what's needed at runtime.

## AI Model (Phase 5.0)

The AI model (~2.5 GB GGUF) is **not bundled**. It is downloaded on first AI use via `ai.model_manager.ensure_model()`. Users need ~3 GB free disk space.

The app works fully without the AI model — the AI query section is disabled until the model is present (or Ollama is running locally).

## Distributing to Users — Unsigned App Warnings

Windows SmartScreen will warn about the unsigned executable. Users can bypass:

1. Right-click `mrma.exe` → **Properties**
2. Check **Unblock** → **OK**
3. Or: Click **More info → Run anyway** in the SmartScreen dialog

macOS Gatekeeper:
```bash
xattr -d com.apple.quarantine MRMA-portable-mac/mrma
```

## Running Tests

```powershell
python -m pytest tests/ -v
```

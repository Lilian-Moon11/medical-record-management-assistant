# Contributing to Medical Record Management Assistant

Thank you for your interest in Medical Record Management Assistant! 

This project is a dedicated, local-first medical record management tool. I am currently focused on other priorities, which means this repository is provided **"as is"** and is not actively seeking new features. 

However, because this is an AGPLv3 open-sourced project, you are completely free to fork the repository, build upon it, and take the concept in your own direction!

If you do wish to submit a PR to the main repository, please keep in mind that PR reviews may be significantly delayed.

## Local Development Setup

To get the codebase running locally:

### 1. Prerequisites
- **Python 3.12** is strictly required. Newer or older versions of Python may cause conflicts with the PyTorch/AI dependencies (such as the local ONNX/Surya parsing models).

### 2. Virtual Environment
Please create a virtual environment to isolate the pipeline dependencies:

**Windows:**
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\activate
```

**Mac/Linux:**
```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

### 3. Dependencies
Install all required modules:
```bash
pip install -r requirements.txt
```

### 4. Running the App
Run the application via the entry point:
```bash
python main.py
```

## Testing Requirements

Before contributing code, you must ensure that all test suites pass. The test architecture relies on the standard `unittest` library.

**To run the test suite:**
```bash
python -m unittest discover tests
```

Ensure no new warnings are generated.

## Submitting Pull Requests
If you submit a Pull Request, please ensure you use the provided Pull Request Template. A brief overview:
1. Ensure your code does not contain hard-coded paths (`c:\...` or `/home/...`). Always use the relative helpers in `core/paths.py`.
2. Ensure you have no debug `print()` statements; utilize the global `logger` in `core/logger.py`.
3. Provide screenshots if you altered Flet UI elements.

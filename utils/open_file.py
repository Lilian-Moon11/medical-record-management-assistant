# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import os
import sys
import subprocess

def open_file_cross_platform(path: str) -> None:
    """Open a file with the default OS handler in a cross-platform way."""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def decrypt_and_open_document(page, file_name: str = None, patient_id: int = None, doc_id: int = None) -> None:
    """Look up an encrypted document, decrypt it to a temp file,
    and open it with the OS default handler.

    Lookup can be done by *file_name* (original behaviour) or by *doc_id*
    (primary key). When doc_id is provided, file_name is ignored.

    Parameters
    ----------
    page : ft.Page
        The Flet page (needs ``db_connection`` and ``db_key_raw``).
    file_name : str, optional
        The ``file_name`` column value from the ``documents`` table.
    doc_id : int, optional
        The ``id`` primary key of the document row.
    patient_id : int, optional
        If not provided, ``page.current_profile[0]`` is used.
    """
    import flet as ft
    import tempfile
    import time as _time
    from crypto.file_crypto import get_or_create_file_master_key, decrypt_bytes
    from core.paths import resolve_doc_path
    from utils.ui_helpers import show_snack

    if patient_id is None:
        patient_id = page.current_profile[0]

    try:
        cur = page.db_connection.cursor()

        if doc_id is not None:
            cur.execute(
                "SELECT file_name, file_path FROM documents WHERE id=?",
                (doc_id,),
            )
            r = cur.fetchone()
            if not r:
                show_snack(page, "Source document not found.", ft.Colors.RED)
                return
            file_name, file_path = r[0], r[1]
        else:
            cur.execute(
                "SELECT file_path FROM documents WHERE patient_id=? AND file_name=? ORDER BY id DESC LIMIT 1",
                (patient_id, file_name),
            )
            r = cur.fetchone()
            if not r or not r[0]:
                show_snack(page, "Source file not found.", ft.Colors.RED)
                return
            file_path = r[0]

        resolved = str(resolve_doc_path(file_path))
        if not os.path.exists(resolved):
            show_snack(page, "Source file not found.", ft.Colors.RED)
            return
        fmk = get_or_create_file_master_key(page.db_connection, dmk_raw=page.db_key_raw)
        with open(resolved, "rb") as f:
            ciphertext = f.read()
        plaintext = decrypt_bytes(fmk, ciphertext)
        _, ext = os.path.splitext(file_name or "file.pdf")
        tmp = os.path.join(tempfile.gettempdir(), f"mrma_dec_{int(_time.time())}{ext or '.pdf'}")
        with open(tmp, "wb") as f:
            f.write(plaintext)
        open_file_cross_platform(tmp)
        show_snack(page, f"Opened {file_name}", ft.Colors.BLUE)
    except Exception as ex:
        show_snack(page, f"Open failed: {ex}", ft.Colors.RED)


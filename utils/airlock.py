# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Import/Export "airlock" for portable data transfer.
#
# Provides two public functions:
#   - export_profile():  Dumps all patient data + decrypted documents into an
#                        AES-256 encrypted ZIP file, using the vault password.
#   - import_profile():  Reads an airlock ZIP, inserts data into the current
#                        vault, and re-encrypts documents with the new FMK.
#
# The ZIP is encrypted with the same password used for the vault, so the user
# only ever has to remember one password.
#
# Design goals:
#   - Zero new passwords (reuse vault password)
#   - Accessible on slow/old hardware (minimal memory; streams files)
#   - Simple for low-tech-literacy users (one button, one file)
#   - Standard ZIP format openable by 7-Zip / WinRAR for transparency
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import os
from datetime import datetime

import pyzipper

from crypto.file_crypto import (
    get_or_create_file_master_key,
    decrypt_bytes,
    encrypt_bytes,
)

AIRLOCK_VERSION = 1
MANIFEST_NAME = "manifest.json"


# ── helpers ──────────────────────────────────────────────────────────────────

def _now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _fetch_all_as_dicts(cur, sql, params=()):
    """Run a query and return rows as a list of dicts (column-name keyed)."""
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── EXPORT ───────────────────────────────────────────────────────────────────

def export_profile(conn, dmk_raw: bytes, data_dir: str,
                   dest_path: str, zip_password: str) -> str:
    """
    Export the entire patient profile to an AES-256 encrypted ZIP.

    Parameters
    ----------
    conn        : open (unlocked) database connection
    dmk_raw     : 32-byte Database Master Key (for FMK unwrap)
    data_dir    : root data directory (parent of patient sub-dirs)
    dest_path   : full path for the output .zip file
    zip_password: password to encrypt the ZIP (typically the vault password)

    Returns
    -------
    dest_path on success.
    """
    fmk = get_or_create_file_master_key(conn, dmk_raw=dmk_raw)
    cur = conn.cursor()

    # ---- gather structured data from all tables ----
    manifest = {
        "airlock_version": AIRLOCK_VERSION,
        "exported_at": _now_ts(),
        "patients": _fetch_all_as_dicts(
            cur, "SELECT id, name, dob, notes FROM patients"),
        "field_definitions": _fetch_all_as_dicts(
            cur, "SELECT field_key, label, data_type, category, is_sensitive, created_at "
                 "FROM field_definitions"),
        "patient_field_values": _fetch_all_as_dicts(
            cur, "SELECT patient_id, field_key, value_text, source, updated_at "
                 "FROM patient_field_values"),
        "providers": _fetch_all_as_dicts(
            cur, "SELECT id, patient_id, name, specialty, clinic, phone, fax, "
                 "email, address, notes, created_at, updated_at FROM providers"),
        "lab_reports": _fetch_all_as_dicts(
            cur, "SELECT id, patient_id, source_document_id, collected_date, "
                 "reported_date, ordering_provider, facility, notes, "
                 "created_at, updated_at FROM lab_reports"),
        "lab_results": _fetch_all_as_dicts(
            cur, "SELECT id, patient_id, report_id, test_name, value_text, "
                 "value_num, unit, ref_range_text, ref_low, ref_high, ref_unit, "
                 "abnormal_flag, result_date, notes, created_at, updated_at "
                 "FROM lab_results"),
        "documents": _fetch_all_as_dicts(
            cur, "SELECT id, patient_id, file_name, file_path, parsed_text, "
                 "upload_date FROM documents"),
        "files": [],   # filled below
    }

    # ---- decrypt each .enc file and stage for ZIP ----
    file_entries = []
    for doc in manifest["documents"]:
        enc_path = doc.get("file_path", "")
        if not enc_path:
            continue
        full_enc = os.path.join(os.path.dirname(data_dir), enc_path) \
            if not os.path.isabs(enc_path) else enc_path
        if not os.path.isfile(full_enc):
            # best-effort: skip missing files
            continue
        # zip-internal path: files/<patient_id>/<original_name>
        zip_name = f"files/{doc['patient_id']}/{doc['file_name']}"
        file_entries.append({
            "doc_id": doc["id"],
            "zip_name": zip_name,
            "enc_path": full_enc,
        })

    manifest["files"] = [
        {"doc_id": fe["doc_id"], "zip_name": fe["zip_name"]}
        for fe in file_entries
    ]

    # ---- write the ZIP ----
    pwd_bytes = zip_password.encode("utf-8")
    with pyzipper.AESZipFile(dest_path, "w",
                             compression=pyzipper.ZIP_DEFLATED,
                             encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(pwd_bytes)

        # manifest first
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))

        # then each decrypted document
        for fe in file_entries:
            with open(fe["enc_path"], "rb") as f:
                ciphertext = f.read()
            plaintext = decrypt_bytes(fmk, ciphertext)
            zf.writestr(fe["zip_name"], plaintext)

    return dest_path


# ── IMPORT ───────────────────────────────────────────────────────────────────

def peek_manifest(zip_path: str, zip_password: str) -> dict:
    """
    Open an airlock ZIP and return the parsed manifest without importing.
    Useful for showing the user what they're about to import.
    """
    pwd_bytes = zip_password.encode("utf-8")
    with pyzipper.AESZipFile(zip_path, "r") as zf:
        zf.setpassword(pwd_bytes)
        raw = zf.read(MANIFEST_NAME)
    return json.loads(raw)


def import_profile(conn, dmk_raw: bytes, data_dir: str,
                   zip_path: str, zip_password: str) -> dict:
    """
    Import an airlock ZIP into the current vault.

    Returns a summary dict with counts of imported items.
    """
    fmk = get_or_create_file_master_key(conn, dmk_raw=dmk_raw)
    cur = conn.cursor()
    pwd_bytes = zip_password.encode("utf-8")
    now = _now_ts()

    with pyzipper.AESZipFile(zip_path, "r") as zf:
        zf.setpassword(pwd_bytes)
        manifest = json.loads(zf.read(MANIFEST_NAME))

        if manifest.get("airlock_version", 0) > AIRLOCK_VERSION:
            raise ValueError(
                "This backup was created with a newer version of the software. "
                "Please update before importing."
            )

        # ---- ID remapping ----
        # Old IDs in the ZIP may collide with IDs in the current vault,
        # so we track old→new mappings.
        patient_id_map = {}    # old_id → new_id
        report_id_map = {}     # old_id → new_id
        doc_id_map = {}        # old_id → new_id

        counts = {
            "patients": 0,
            "field_definitions": 0,
            "patient_field_values": 0,
            "providers": 0,
            "lab_reports": 0,
            "lab_results": 0,
            "documents": 0,
            "files": 0,
        }

        # ── patients ──
        for p in manifest.get("patients", []):
            old_id = p["id"]
            cur.execute(
                "INSERT INTO patients (name, dob, notes) VALUES (?, ?, ?)",
                (p.get("name"), p.get("dob"), p.get("notes")),
            )
            patient_id_map[old_id] = cur.lastrowid
            counts["patients"] += 1

        # ── field_definitions (idempotent) ──
        for fd in manifest.get("field_definitions", []):
            cur.execute(
                "INSERT OR IGNORE INTO field_definitions "
                "(field_key, label, data_type, category, is_sensitive, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fd["field_key"], fd["label"], fd.get("data_type", "text"),
                 fd.get("category", "General"), fd.get("is_sensitive", 0),
                 fd.get("created_at", now)),
            )
            counts["field_definitions"] += 1

        # ── patient_field_values ──
        for pf in manifest.get("patient_field_values", []):
            new_pid = patient_id_map.get(pf["patient_id"])
            if new_pid is None:
                continue
            cur.execute(
                "INSERT INTO patient_field_values "
                "(patient_id, field_key, value_text, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(patient_id, field_key) DO UPDATE SET "
                "value_text=excluded.value_text, source=excluded.source, "
                "updated_at=excluded.updated_at",
                (new_pid, pf["field_key"], pf.get("value_text"),
                 pf.get("source", "import"), pf.get("updated_at", now)),
            )
            counts["patient_field_values"] += 1

        # ── providers ──
        for prov in manifest.get("providers", []):
            new_pid = patient_id_map.get(prov["patient_id"])
            if new_pid is None:
                continue
            cur.execute(
                "INSERT INTO providers "
                "(patient_id, name, specialty, clinic, phone, fax, email, "
                "address, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_pid, prov.get("name"), prov.get("specialty"),
                 prov.get("clinic"), prov.get("phone"), prov.get("fax"),
                 prov.get("email"), prov.get("address"), prov.get("notes"),
                 prov.get("created_at", now), prov.get("updated_at", now)),
            )
            counts["providers"] += 1

        # ── lab_reports ──
        for lr in manifest.get("lab_reports", []):
            old_id = lr["id"]
            new_pid = patient_id_map.get(lr["patient_id"])
            if new_pid is None:
                continue
            # source_document_id will be remapped after documents are inserted
            cur.execute(
                "INSERT INTO lab_reports "
                "(patient_id, collected_date, reported_date, ordering_provider, "
                "facility, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_pid, lr.get("collected_date"), lr.get("reported_date"),
                 lr.get("ordering_provider"), lr.get("facility"),
                 lr.get("notes"), lr.get("created_at", now),
                 lr.get("updated_at", now)),
            )
            report_id_map[old_id] = cur.lastrowid
            counts["lab_reports"] += 1

        # ── lab_results ──
        for res in manifest.get("lab_results", []):
            new_pid = patient_id_map.get(res["patient_id"])
            new_rid = report_id_map.get(res["report_id"])
            if new_pid is None or new_rid is None:
                continue
            cur.execute(
                "INSERT INTO lab_results "
                "(patient_id, report_id, test_name, value_text, value_num, "
                "unit, ref_range_text, ref_low, ref_high, ref_unit, "
                "abnormal_flag, result_date, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_pid, new_rid, res.get("test_name"), res.get("value_text"),
                 res.get("value_num"), res.get("unit"),
                 res.get("ref_range_text"), res.get("ref_low"),
                 res.get("ref_high"), res.get("ref_unit"),
                 res.get("abnormal_flag"), res.get("result_date"),
                 res.get("notes"), res.get("created_at", now),
                 res.get("updated_at", now)),
            )
            counts["lab_results"] += 1

        # ── documents (metadata + re-encrypted files) ──
        # build a lookup: old_doc_id → zip_name
        file_lookup = {
            fe["doc_id"]: fe["zip_name"]
            for fe in manifest.get("files", [])
        }

        for doc in manifest.get("documents", []):
            old_id = doc["id"]
            new_pid = patient_id_map.get(doc["patient_id"])
            if new_pid is None:
                continue

            # prepare filesystem destination
            patient_dir = os.path.join(data_dir, str(new_pid))
            os.makedirs(patient_dir, exist_ok=True)

            file_name = doc.get("file_name", f"doc_{old_id}")
            enc_name = file_name + ".enc" if not file_name.endswith(".enc") else file_name
            dest_enc = os.path.join(patient_dir, enc_name)
            rel_path = os.path.join("data", str(new_pid), enc_name)

            # read plaintext from ZIP, re-encrypt with this vault's FMK
            zip_name = file_lookup.get(old_id)
            if zip_name and zip_name in zf.namelist():
                plaintext = zf.read(zip_name)
                ciphertext = encrypt_bytes(fmk, plaintext)
                with open(dest_enc, "wb") as f:
                    f.write(ciphertext)
                counts["files"] += 1

            cur.execute(
                "INSERT INTO documents "
                "(patient_id, file_name, file_path, parsed_text, upload_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (new_pid, doc.get("file_name"), rel_path,
                 doc.get("parsed_text"), doc.get("upload_date", now)),
            )
            doc_id_map[old_id] = cur.lastrowid
            counts["documents"] += 1

        # ── fix up lab_reports.source_document_id ──
        for lr in manifest.get("lab_reports", []):
            old_doc_id = lr.get("source_document_id")
            if old_doc_id and old_doc_id in doc_id_map:
                new_report_id = report_id_map.get(lr["id"])
                if new_report_id:
                    cur.execute(
                        "UPDATE lab_reports SET source_document_id = ? "
                        "WHERE id = ?",
                        (doc_id_map[old_doc_id], new_report_id),
                    )

        conn.commit()

    return counts

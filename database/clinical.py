# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Database access layer for patient-linked clinical records.
#
# This module provides lightweight SQLite CRUD helpers for three data domains:
# - Providers: list/create/update/delete provider directory entries tied to a
#   specific patient_id, with optional search and stable ordering.
# - Labs: list/create/update/delete lab reports and their child lab results,
#   including helpers to list/add/update/delete results for a given report.
# - Documents: list/add/delete uploaded patient documents and fetch document
#   metadata.
#
# Conventions:
# - All operations are scoped by `patient_id` to prevent cross-patient updates.
# - Writes automatically set/refresh `created_at` and `updated_at` timestamps
#   using a consistent "%Y-%m-%d %H:%M" format.
# - Optional search parameters are implemented via SQL LIKE patterns.
#
# Note:
# - `delete_lab_report()` performs a cascading delete by removing associated
#   lab_results before deleting the parent lab_reports row.
# -----------------------------------------------------------------------------

from datetime import datetime

def _now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def _like(s: str):
    return f"%{s.strip()}%"

# Providers
def list_providers(conn, patient_id, search=None, limit=200):
    cur = conn.cursor()
    params = [patient_id]
    sql = "SELECT id, name, specialty, clinic, phone, fax, email, address, notes, created_at, updated_at FROM providers WHERE patient_id = ?"
    if search:
        q = _like(search)
        sql += " AND (name LIKE ? OR clinic LIKE ? OR specialty LIKE ? OR phone LIKE ?)"
        params.extend([q, q, q, q])
    sql += " ORDER BY name COLLATE NOCASE ASC LIMIT ?"
    params.append(limit)
    cur.execute(sql, tuple(params))
    return cur.fetchall()

def create_provider(conn, patient_id, **kwargs):
    cur = conn.cursor()
    now = _now_ts()
    cols = ["patient_id", "created_at", "updated_at"] + list(kwargs.keys())
    vals = [patient_id, now, now] + list(kwargs.values())
    sql = f"INSERT INTO providers ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})"
    cur.execute(sql, tuple(vals))
    conn.commit()
    return cur.lastrowid

def update_provider(conn, patient_id, provider_id, **kwargs):
    cur = conn.cursor()
    kwargs['updated_at'] = _now_ts()
    set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    sql = f"UPDATE providers SET {set_clause} WHERE id = ? AND patient_id = ?"
    cur.execute(sql, list(kwargs.values()) + [provider_id, patient_id])
    conn.commit()
    return cur.rowcount

def delete_provider(conn, patient_id, provider_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM providers WHERE id = ? AND patient_id = ?", (provider_id, patient_id))
    conn.commit()
    return cur.rowcount

# Labs
def list_lab_results_for_report(conn, patient_id, report_id, search_test=None, limit=500):
    cur = conn.cursor()
    params = [patient_id, report_id]
    sql = "SELECT id, test_name, value_text, value_num, unit, ref_range_text, ref_low, ref_high, ref_unit, abnormal_flag, result_date, notes, created_at, updated_at FROM lab_results WHERE patient_id = ? AND report_id = ?"
    if search_test:
        sql += " AND test_name LIKE ?"
        params.append(_like(search_test))
    sql += " ORDER BY test_name ASC LIMIT ?"
    params.append(limit)
    cur.execute(sql, tuple(params))
    return cur.fetchall()

def list_lab_reports(conn, patient_id, search=None, limit=500):
    cur = conn.cursor()
    params = [patient_id]
    sql = """
        SELECT id, source_document_id, collected_date, reported_date, 
               ordering_provider, facility, notes, created_at, updated_at 
        FROM lab_reports 
        WHERE patient_id = ?
    """
    if search:
        q = _like(search)
        sql += " AND (facility LIKE ? OR ordering_provider LIKE ? OR notes LIKE ?)"
        params.extend([q, q, q])
    
    sql += " ORDER BY collected_date DESC, id DESC LIMIT ?"
    params.append(limit)
    
    cur.execute(sql, tuple(params))
    return cur.fetchall()

def create_lab_report(conn, patient_id, **kwargs):
    cur = conn.cursor()
    now = _now_ts()
    cols = ["patient_id", "created_at", "updated_at"] + list(kwargs.keys())
    vals = [patient_id, now, now] + list(kwargs.values())
    sql = f"INSERT INTO lab_reports ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})"
    cur.execute(sql, tuple(vals))
    conn.commit()
    return cur.lastrowid

def update_lab_report(conn, patient_id, report_id, **kwargs):
    cur = conn.cursor()
    kwargs['updated_at'] = _now_ts()
    set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    sql = f"UPDATE lab_reports SET {set_clause} WHERE id = ? AND patient_id = ?"
    cur.execute(sql, list(kwargs.values()) + [report_id, patient_id])
    conn.commit()
    return cur.rowcount

def delete_lab_report(conn, patient_id, report_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM lab_results WHERE report_id = ? AND patient_id = ?", (report_id, patient_id))
    cur.execute("DELETE FROM lab_reports WHERE id = ? AND patient_id = ?", (report_id, patient_id))
    conn.commit()
    return cur.rowcount

def add_lab_result(conn, patient_id, report_id, **kwargs):
    cur = conn.cursor()
    now = _now_ts()
    cols = ["patient_id", "report_id", "created_at", "updated_at"] + list(kwargs.keys())
    vals = [patient_id, report_id, now, now] + list(kwargs.values())
    sql = f"INSERT INTO lab_results ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})"
    cur.execute(sql, tuple(vals))
    conn.commit()
    return cur.lastrowid

def update_lab_result(conn, patient_id, report_id, result_id, **kwargs):
    cur = conn.cursor()
    kwargs['updated_at'] = _now_ts()
    set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    sql = f"UPDATE lab_results SET {set_clause} WHERE id = ? AND patient_id = ? AND report_id = ?"
    cur.execute(sql, list(kwargs.values()) + [result_id, patient_id, report_id])
    conn.commit()
    return cur.rowcount

def delete_lab_result(conn, patient_id, result_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM lab_results WHERE id = ? AND patient_id = ?", (result_id, patient_id))
    conn.commit()
    return cur.rowcount

# Test-centric queries (for Labs redesign)
def list_distinct_test_names(conn, patient_id, search=None, category=None):
    """Return sorted unique test names across all lab_results for a patient."""
    cur = conn.cursor()
    params = [patient_id]
    sql = "SELECT DISTINCT test_name FROM lab_results WHERE patient_id = ?"
    if category:
        sql += " AND category = ?"
        params.append(category)
    if search:
        sql += " AND test_name LIKE ?"
        params.append(_like(search))
    sql += " ORDER BY test_name COLLATE NOCASE ASC"
    cur.execute(sql, tuple(params))
    return [row[0] for row in cur.fetchall()]

def list_all_results_for_test(conn, patient_id, test_name, category=None):
    """Return all lab_results rows for a given test_name across all reports,
    joined with lab_reports for source_document_id and collected_date.
    Ordered by result_date ASC for chronological charting.
    
    Returns rows shaped:
    (result_id, test_name, value_text, value_num, unit, ref_range_text,
     ref_low, ref_high, ref_unit, abnormal_flag, result_date, notes,
     report_id, source_document_id, collected_date, created_at, updated_at)
    """
    cur = conn.cursor()
    sql = """
        SELECT r.id, r.test_name, r.value_text, r.value_num, r.unit,
               r.ref_range_text, r.ref_low, r.ref_high, r.ref_unit,
               r.abnormal_flag, r.result_date, r.notes,
               r.report_id, lr.source_document_id, lr.collected_date,
               r.created_at, r.updated_at
        FROM lab_results r
        LEFT JOIN lab_reports lr ON lr.id = r.report_id AND lr.patient_id = r.patient_id
        WHERE r.patient_id = ? AND r.test_name = ?
    """
    params = [patient_id, test_name]
    if category:
        sql += " AND r.category = ?"
        params.append(category)
        
    sql += " ORDER BY COALESCE(r.result_date, lr.collected_date) ASC, r.id ASC"
    
    cur.execute(sql, tuple(params))
    return cur.fetchall()

# Documents
def get_patient_documents(conn, patient_id):
    cur = conn.cursor()
    cur.execute("SELECT id, file_name, upload_date, file_path, visit_date, specialty FROM documents WHERE patient_id = ? ORDER BY id DESC", (patient_id,))
    return cur.fetchall()

def add_document(conn, patient_id, file_name, file_path, upload_date):
    cur = conn.cursor()
    cur.execute("INSERT INTO documents (patient_id, file_name, file_path, upload_date) VALUES (?, ?, ?, ?)", (patient_id, file_name, file_path, upload_date))
    conn.commit()
    return cur.lastrowid

def delete_document(conn, document_id):
    cur = conn.cursor()
    # Cascade delete AI data so deleted files don't haunt the Chat Assistant
    cur.execute("DELETE FROM document_chunks WHERE doc_id = ?", (document_id,))
    cur.execute("DELETE FROM ai_extraction_inbox WHERE doc_id = ?", (document_id,))
    cur.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()

def get_document_metadata(conn, document_id):
    cur = conn.cursor()
    cur.execute("SELECT file_name, file_path, upload_date FROM documents WHERE id = ?", (document_id,))
    return cur.fetchone()
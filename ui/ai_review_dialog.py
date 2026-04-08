"""
PURPOSE:
Provides the UI dialog for reviewing and accepting/rejecting AI-extracted data.
Handles "Structured Pushing" by mapping raw AI JSON responses directly into the 
application's complex JSON lists (like conditions or medications) natively found 
on the Health Record tab.

When accepted, the AI's data is flawlessly mapped and injected rather than 
floating as orphaned strings in the database. Also mitigates Flet dark mode 
UI issues by enforcing specific text colors.
"""

import flet as ft
from utils.ui_helpers import pt_scale, show_snack
import json
def fetch_pending_suggestions(conn, patient_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, doc_id, field_key, suggested_value, confidence, source_file_name, conflict, existing_value
        FROM ai_extraction_inbox
        WHERE patient_id = ? AND status = 'pending'
        """,
        (patient_id,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def mark_suggestion(conn, suggestion_id: int, status: str):
    cur = conn.cursor()
    cur.execute(
        "UPDATE ai_extraction_inbox SET status = ? WHERE id = ?",
        (status, suggestion_id)
    )
    conn.commit()

def apply_suggestion(conn, patient_id: int, s: dict):
    # Intercept non-EAV domains first
    if s["field_key"] == "providers.list":
        try:
            val_obj = json.loads(s["suggested_value"])
        except Exception:
            return
            
        from database.clinical import create_provider
        create_provider(
            conn, 
            patient_id, 
            name=val_obj.get("name", "Unknown Provider"),
            specialty=val_obj.get("specialty"),
            clinic=val_obj.get("clinic"),
            phone=val_obj.get("phone"),
            fax=val_obj.get("fax"),
            address=val_obj.get("address")
        )
        return

    if s["field_key"] in ("vitals.list", "lab_results.list"):
        doc_id = s.get("doc_id")
        report_id = None
        cur = conn.cursor()
        if doc_id:
            cur.execute("SELECT id FROM lab_reports WHERE source_document_id = ? AND patient_id = ?", (doc_id, patient_id))
            r = cur.fetchone()
            if r:
                report_id = r[0]
                
        from database.clinical import create_lab_report, add_lab_result
        if not report_id:
            import datetime
            today = datetime.date.today().isoformat()
            report_id = create_lab_report(conn, patient_id, source_document_id=doc_id, collected_date=today, reported_date=today, notes="AI Extracted")
            
        try:
            val_obj = json.loads(s["suggested_value"])
        except Exception:
            return
            
        def _parse_value_num(t):
            if not t: return None
            t = str(t).strip()
            if any(sym in t for sym in ("<", ">", "<=", ">=")): return None
            import re
            m = re.search(r"[-+]?\d[\d,]*\.?\d*", t)
            if not m: return None
            try: return float(m.group(0).replace(",", ""))
            except: return None
            
        value_text = str(val_obj.get("value") or val_obj.get("value_text", "")).strip()
        category = "Vitals" if s["field_key"] == "vitals.list" else "Lab"
        
        add_lab_result(
            conn,
            patient_id,
            report_id,
            test_name=val_obj.get("name", "Unknown"),
            value_text=value_text,
            value_num=_parse_value_num(value_text),
            unit=val_obj.get("unit"),
            abnormal_flag=val_obj.get("abnormal_flag"),
            result_date=val_obj.get("date"),
            category=category
        )
        return

    # Map simple AI field keys into structured JSON list targets
    key_map = {
        "condition.name": ("conditions.list", "name"),
        "medication.name": ("medicationstatement.current_list", "name"),
        "surgery.name": ("procedures.list", "name"),
        "allergyintolerance.list": ("allergyintolerance.list", "substance")
    }
    
    if s["field_key"] in key_map:
        target_list, target_prop = key_map[s["field_key"]]
        s["field_key"] = target_list
        
        # Only rewrite the value if it's not already serialized JSON
        if not str(s["suggested_value"]).strip().startswith("{"):
            s["suggested_value"] = json.dumps({target_prop: s["suggested_value"]})

    cur = conn.cursor()
    now_str = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
    
    cur.execute("SELECT data_type FROM field_definitions WHERE field_key = ?", (s["field_key"],))
    row = cur.fetchone()
    data_type = row[0] if row else "text"
    
    if data_type == "json":
        cur.execute("SELECT value_text FROM patient_field_values WHERE patient_id=? AND field_key=?", (patient_id, s["field_key"]))
        ex_row = cur.fetchone()
        current_list = []
        if ex_row and ex_row[0]:
            try:
                current_list = json.loads(ex_row[0])
            except:
                pass
        if not isinstance(current_list, list):
            current_list = []
        
        try:
            new_val_obj = json.loads(s["suggested_value"])
        except:
            new_val_obj = {"value": s["suggested_value"], "_ai_source": s["source_file_name"]}
            
        # Merge/Update logic for lists: avoid duplicates if names match
        updated = False
        if isinstance(new_val_obj, dict):
            pk = "name"
            if "allergy" in s["field_key"]: pk = "substance"
            if "insurance" in s["field_key"]: pk = "provider"
            
            new_name = str(new_val_obj.get(pk, "")).strip().lower()
            if new_name:
                for i, ex_item in enumerate(current_list):
                    if isinstance(ex_item, dict) and str(ex_item.get(pk, "")).strip().lower() == new_name:
                        # Ensure we don't lose user's existing data if AI didn't return it
                        merged = dict(ex_item)
                        for k, v in new_val_obj.items():
                            if v and str(v).lower() != "none" and str(v).strip() != "":
                                merged[k] = v
                        merged["_ai_source"] = s["source_file_name"]
                        merged["_source"] = "ai"
                        merged["_updated"] = now_str
                        current_list[i] = merged
                        updated = True
                        break
                        
        if not updated:
            new_val_obj["_ai_source"] = s["source_file_name"]
            new_val_obj["_source"] = "ai"
            new_val_obj["_updated"] = now_str
            current_list.append(new_val_obj)
            
        final_str = json.dumps(current_list)
    else:
        final_str = s["suggested_value"]
        
    if data_type == "json":
        # For list fields, only update value_text — per-item provenance lives inside the JSON
        cur.execute(
            """
            INSERT INTO patient_field_values 
            (patient_id, field_key, value_text, source, source_doc_id, ai_confidence, updated_at)
            VALUES (?, ?, ?, 'ai', ?, ?, ?)
            ON CONFLICT(patient_id, field_key) DO UPDATE SET
                value_text=excluded.value_text
            """,
            (patient_id, s["field_key"], final_str, s.get("doc_id"), s["confidence"], now_str) 
        )
    else:
        # For scalar fields, update all provenance columns
        cur.execute(
            """
            INSERT INTO patient_field_values 
            (patient_id, field_key, value_text, source, source_doc_id, ai_confidence, updated_at)
            VALUES (?, ?, ?, 'ai', ?, ?, ?)
            ON CONFLICT(patient_id, field_key) DO UPDATE SET
                value_text=excluded.value_text,
                source=excluded.source,
                source_doc_id=excluded.source_doc_id,
                ai_confidence=excluded.ai_confidence,
                updated_at=excluded.updated_at
            """,
            (patient_id, s["field_key"], final_str, s.get("doc_id"), s["confidence"], now_str) 
        )
    conn.commit()

def show_ai_review_dialog(page: ft.Page, patient_id: int, on_close=None):
    conn = page.db_connection
    suggestions = fetch_pending_suggestions(conn, patient_id)
    
    if not suggestions:
        show_snack(page, "No pending AI suggestions.", "green")
        if on_close: on_close()
        return

    list_view = ft.ListView(spacing=15, padding=10, expand=True, auto_scroll=False)
    
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Review Extraction Suggestions", weight="bold"),
        content=ft.Container(
            width=600,
            height=400,
            content=list_view
        ),
        actions=[],
    )
    
    def _close(_e):
        dlg.open = False
        page.update()
        if on_close: on_close()
        
    dlg.actions.append(ft.TextButton("Close", on_click=_close))
    
    def refresh_list():
        list_view.controls.clear()
        remaining = fetch_pending_suggestions(conn, patient_id)
        if not remaining:
            _close(None)
            return
            
        for s in remaining:
            def accept_click(e, sg=s):
                apply_suggestion(conn, patient_id, sg)
                mark_suggestion(conn, sg["id"], "accepted")
                show_snack(page, f"Accepted {sg['field_key']}", "green")
                refresh_list()
                
            def reject_click(e, sg=s):
                mark_suggestion(conn, sg["id"], "rejected")
                show_snack(page, f"Rejected {sg['field_key']}", "orange")
                refresh_list()

            # 1. Human-Readable formatting for JSON payload
            def _pretty_key(k):
                """Turn compound/camelCase keys into readable labels."""
                import re
                k = str(k).replace("_", " ")
                # Insert space before uppercase letters in camelCase
                k = re.sub(r'([a-z])([A-Z])', r'\1 \2', k)
                # Handle fully-lowercase compound words
                _compounds = {
                    "datediagnosed": "Date Diagnosed",
                    "date diagnosed": "Date Diagnosed",
                    "ai source": "AI Source",
                }
                if k.strip().lower() in _compounds:
                    return _compounds[k.strip().lower()]
                return k.strip().title()
            
            def _format_existing(raw):
                """Format raw JSON existing value into human-readable text."""
                try:
                    obj = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(obj, dict):
                        parts = [f"{_pretty_key(k)}: {v}" for k, v in obj.items() if v and str(v).lower() != "none" and not str(k).startswith("_")]
                        return ", ".join(parts) if parts else str(raw)
                    elif isinstance(obj, list):
                        items = []
                        for item in obj:
                            if isinstance(item, dict):
                                parts = [f"{_pretty_key(k)}: {v}" for k, v in item.items() if v and str(v).lower() != "none" and not str(k).startswith("_")]
                                items.append(", ".join(parts))
                            else:
                                items.append(str(item))
                        return " | ".join(items) if items else str(raw)
                except Exception:
                    pass
                return str(raw)

            # Quality warning cards (from ingestion quality flagging)
            if s.get("field_key") == "system.quality_warning":
                def dismiss_click(e, sg=s):
                    mark_suggestion(conn, sg["id"], "dismissed")
                    refresh_list()

                warning_item = ft.Container(
                    bgcolor=ft.Colors.AMBER_50,
                    border=ft.border.all(1, ft.Colors.AMBER_400),
                    border_radius=8,
                    padding=10,
                    content=ft.Column([
                        ft.Row([
                            ft.Icon(ft.Icons.INFO_OUTLINE, color=ft.Colors.AMBER_800),
                            ft.Text("Document Quality Notice", weight="bold", color=ft.Colors.AMBER_900),
                            ft.Container(expand=True),
                            ft.Text(f"Source: {s['source_file_name']}", color=ft.Colors.GREY_600, size=pt_scale(page, 12)),
                        ]),
                        ft.Text(s["suggested_value"], size=pt_scale(page, 13), color=ft.Colors.AMBER_900),
                        ft.Row([
                            ft.TextButton("Dismiss", on_click=dismiss_click),
                        ], alignment=ft.MainAxisAlignment.END),
                    ])
                )
                list_view.controls.append(warning_item)
                continue

            try:
                parsed_val = json.loads(s["suggested_value"])
                def _d2s(d):
                    return " • " + ", ".join(f"{_pretty_key(k)}: {v}" for k, v in d.items() if v and str(v).lower() != "none" and not str(k).startswith("_"))
                
                if isinstance(parsed_val, list):
                    display_text = "\n".join(_d2s(i) if isinstance(i, dict) else f" • {i}" for i in parsed_val)
                elif isinstance(parsed_val, dict):
                    display_text = _d2s(parsed_val)
                else:
                    display_text = str(parsed_val)
            except:
                display_text = s["suggested_value"]

            # 2. Refined conflict logic for empty states
            existing_str = str(s.get('existing_value', '')).strip().lower()
            is_effectively_empty = existing_str in ('none', 'null', '[]', '{}', '')
            
            action_verb = "Add" if is_effectively_empty else "Update"
            conflict_warning = ft.Container()
            
            if s["conflict"] and not is_effectively_empty:
                existing_display = _format_existing(s['existing_value'])
                conflict_warning = ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Icon(ft.Icons.WARNING_AMBER, color=ft.Colors.DEEP_ORANGE_900),
                            ft.Text("Conflict with your existing data:", color=ft.Colors.DEEP_ORANGE_900, weight="bold", size=pt_scale(page, 12))
                        ]),
                        ft.Text(f"Your current record: {existing_display}", color=ft.Colors.DEEP_ORANGE_900, italic=True, size=pt_scale(page, 12)),
                        ft.Text("Accept to use the AI suggestion, or Reject to keep yours.", color=ft.Colors.GREY_700, size=pt_scale(page, 11)),
                    ], spacing=3),
                    bgcolor=ft.Colors.ORANGE_50,
                    padding=8,
                    border_radius=4,
                    margin=ft.margin.only(bottom=5)
                )

            # 3. Allergy enrichment: same substance, no conflict — show what new details will be merged
            if not s["conflict"] and s.get("existing_value") and "allergy" in s.get("field_key", ""):
                try:
                    existing_obj = json.loads(s["existing_value"]) if isinstance(s["existing_value"], str) else s["existing_value"]
                    new_obj = json.loads(s["suggested_value"]) if isinstance(s["suggested_value"], str) else s["suggested_value"]
                    if isinstance(existing_obj, dict) and isinstance(new_obj, dict):
                        diffs = []
                        for k, new_v in new_obj.items():
                            if k.startswith("_"): continue
                            ex_v = str(existing_obj.get(k, "")).strip()
                            new_v_str = str(new_v).strip()
                            if new_v_str.lower() != ex_v.lower() and new_v_str and new_v_str.lower() != "none":
                                if ex_v and ex_v.lower() != "none":
                                    diffs.append(f"{_pretty_key(k)}: {ex_v} → {new_v_str}")
                                else:
                                    diffs.append(f"{_pretty_key(k)}: {new_v_str} (new)")
                        if diffs:
                            action_verb = "Update"
                            conflict_warning = ft.Container(
                                content=ft.Column([
                                    ft.Row([
                                        ft.Icon(ft.Icons.INFO_OUTLINE, color=ft.Colors.BLUE_700),
                                        ft.Text("New details found for this existing allergy:", color=ft.Colors.BLUE_700, weight="bold", size=pt_scale(page, 12))
                                    ]),
                                    *[ft.Text(f"  • {d}", color=ft.Colors.BLUE_700, size=pt_scale(page, 12)) for d in diffs],
                                    ft.Text("Accept to enrich the existing record with this information.", color=ft.Colors.GREY_700, size=pt_scale(page, 11)),
                                ], spacing=3),
                                bgcolor=ft.Colors.BLUE_50,
                                padding=8,
                                border_radius=4,
                                margin=ft.margin.only(bottom=5)
                            )
                except Exception:
                    pass

            # 4. Medication diff: same name conflict — show specific field changes (dose, frequency, etc.)
            if s["conflict"] and not is_effectively_empty and "medication" in s.get("field_key", ""):
                try:
                    existing_obj = json.loads(s["existing_value"]) if isinstance(s["existing_value"], str) else s["existing_value"]
                    new_obj = json.loads(s["suggested_value"]) if isinstance(s["suggested_value"], str) else s["suggested_value"]
                    if isinstance(existing_obj, dict) and isinstance(new_obj, dict):
                        diffs = []
                        for k, new_v in new_obj.items():
                            if k == "name" or k.startswith("_"): continue
                            ex_v = str(existing_obj.get(k, "")).strip()
                            new_v_str = str(new_v).strip()
                            if new_v_str.lower() != ex_v.lower() and new_v_str and new_v_str.lower() != "none":
                                if ex_v and ex_v.lower() != "none":
                                    diffs.append(f"{_pretty_key(k)}: {ex_v} → {new_v_str}")
                                else:
                                    diffs.append(f"{_pretty_key(k)}: {new_v_str} (new)")
                        if diffs:
                            med_name = new_obj.get("name", "medication")
                            conflict_warning = ft.Container(
                                content=ft.Column([
                                    ft.Row([
                                        ft.Icon(ft.Icons.WARNING_AMBER, color=ft.Colors.DEEP_ORANGE_900),
                                        ft.Text(f"Changes detected for {med_name}:", color=ft.Colors.DEEP_ORANGE_900, weight="bold", size=pt_scale(page, 12))
                                    ]),
                                    *[ft.Text(f"  • {d}", color=ft.Colors.DEEP_ORANGE_900, size=pt_scale(page, 12)) for d in diffs],
                                    ft.Text("Accept to update, or Reject to keep current values.", color=ft.Colors.GREY_700, size=pt_scale(page, 11)),
                                ], spacing=3),
                                bgcolor=ft.Colors.ORANGE_50,
                                padding=8,
                                border_radius=4,
                                margin=ft.margin.only(bottom=5)
                            )
                except Exception:
                    pass

            # Prettify the field key (e.g. "medicationstatement.current_list" -> "Medicationstatement Current")
            friendly_name = s['field_key'].split('.')[0].replace("statement", "").replace("intolerance", "").title()

            item = ft.Container(
                bgcolor=ft.Colors.GREY_50,
                border=ft.border.all(1, ft.Colors.GREY_300),
                border_radius=8,
                padding=10,
                content=ft.Column([
                    ft.Row([
                        ft.Text(f"Would you like to {action_verb.lower()} this {friendly_name} information?", weight="bold", color=ft.Colors.BLACK87),
                        ft.Container(expand=True),
                        ft.Text(f"Source: {s['source_file_name']}", color=ft.Colors.GREY_600, size=pt_scale(page, 12))
                    ]),
                    ft.Text(display_text, size=pt_scale(page, 15), color=ft.Colors.BLACK87),
                    conflict_warning,
                    ft.Row([
                        ft.ElevatedButton("Reject", color="red", on_click=reject_click),
                        ft.ElevatedButton("Accept", color="white", bgcolor="green", on_click=accept_click),
                    ], alignment=ft.MainAxisAlignment.END)
                ])
            )
            list_view.controls.append(item)
            
        page.update()

    page.overlay.append(dlg)
    refresh_list()
    dlg.open = True
    page.update()

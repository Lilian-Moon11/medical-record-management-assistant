import flet as ft
import json
from typing import Any, List, Optional
from utils.ui_helpers import append_dialog

def _ensure_sets(page: ft.Page) -> None:
    # Tracks individual row visibility: {"core.name": True, "allergies_1234": False}
    if not hasattr(page.mrma, "_field_vis"):
        page.mrma._field_vis = {}
    # Tracks the last state of a parent panel: {"section.demographics": True}
    if not hasattr(page.mrma, "_panel_vis"):
        page.mrma._panel_vis = {}
    if not hasattr(page.mrma, "_show_source"):
        page.mrma._show_source = False
    if not hasattr(page.mrma, "_show_updated"):
        page.mrma._show_updated = False

def _safe_update(ctrl: Any) -> None:
    try:
        ctrl.update()
    except Exception:
        pass

def _load_json_list(raw: Optional[str]) -> List[dict]:
    try:
        return [x for x in json.loads(raw or "[]") if isinstance(x, dict)]
    except Exception:
        return []

def _migrate_surgeries(items: List[dict]) -> List[dict]:
    """Migrate legacy 'provider' field → 'surgeon' (facility left blank)."""
    for item in items:
        if "provider" in item:
            if not item.get("surgeon"):
                item["surgeon"] = item.pop("provider")
            else:
                del item["provider"]
    return items

def _make_list_delete_dialog(page: ft.Page) -> ft.AlertDialog:
    if hasattr(page.mrma, "_list_delete_dlg"):
        return page.mrma._list_delete_dlg

    dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text("Confirm Delete"),
        content=ft.Text("Are you sure you want to delete this row?"),
        actions=[
            ft.TextButton("Cancel"),
            ft.FilledButton("Delete", icon=ft.Icons.DELETE),
        ],
    )
    page.mrma._list_delete_dlg = dlg
    append_dialog(page, dlg)
    return dlg

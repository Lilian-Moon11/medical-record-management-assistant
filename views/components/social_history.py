# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# ---------------------------------------------------------------------------
# Social History - structured clinical questionnaire using dropdowns
# and multi-select checkboxes where clinically appropriate.
# ---------------------------------------------------------------------------

import flet as ft
from utils.ui_helpers import pt_scale, show_snack, append_dialog, OUTLINE_VARIANT
from views.components.family_helpers import _load_social, _save_social


# ---------------------------------------------------------------------------
# Standard drink definition (used by the info button)
# ---------------------------------------------------------------------------
_DRINK_DEFINITION = (
    "One standard drink equals:\n\n"
    "• 12 oz (355 mL) of regular beer (~5% alcohol)\n"
    "• 5 oz (148 mL) of wine (~12% alcohol)\n"
    "• 1.5 oz (44 mL) of distilled spirits (~40% alcohol)\n\n"
    "These are approximate - actual alcohol content varies by brand."
)


# ---------------------------------------------------------------------------
# Question definitions
#
# "mode" controls how the question is rendered:
#   "dropdown"    - single-select dropdown
#   "multi"       - multi-select checkboxes
#   "text"        - free-text field
# ---------------------------------------------------------------------------
QUESTIONNAIRES: dict[str, dict] = {
    "Alcohol Use": {
        "icon": ft.Icons.LOCAL_BAR,
        "questions": [
            {
                "key": "frequency",
                "label": "How often do you drink alcohol?",
                "mode": "dropdown",
                "options": [
                    "Never",
                    "Monthly or less",
                    "2-4 times a month",
                    "2-3 times a week",
                    "4 or more times a week",
                ],
            },
            {
                "key": "quantity",
                "label": "Typical standard drinks per occasion",
                "mode": "dropdown",
                "info": _DRINK_DEFINITION,
                "options": ["0", "1 or 2", "3 or 4", "5 or 6", "7 to 9", "10 or more"],
            },
            {
                "key": "binge",
                "label": "How often 6+ drinks on one occasion?",
                "mode": "dropdown",
                "options": [
                    "Never",
                    "Less than monthly",
                    "Monthly",
                    "Weekly",
                    "Daily or almost daily",
                ],
            },
        ],
    },
    "Tobacco / Nicotine": {
        "icon": ft.Icons.SMOKING_ROOMS,
        "questions": [
            {
                "key": "status",
                "label": "Tobacco / nicotine use status",
                "mode": "dropdown",
                "options": ["Never used", "Former user", "Current user"],
            },
            {
                "key": "types",
                "label": "Type(s) used (select all that apply)",
                "mode": "multi",
                "options": [
                    "Cigarettes",
                    "Cigars / cigarillos",
                    "Pipe",
                    "Smokeless / chewing tobacco",
                    "Vape / e-cigarette",
                    "Nicotine patches / gum",
                    "Other",
                ],
            },
            {
                "key": "amount",
                "label": "Amount per day (current or former)",
                "mode": "dropdown",
                "options": [
                    "N/A",
                    "Less than 1 per day",
                    "1-5 per day",
                    "6-10 per day",
                    "11-20 per day (about 1 pack)",
                    "More than 1 pack per day",
                ],
            },
        ],
    },
    "Recreational Substances": {
        "icon": ft.Icons.SCIENCE,
        "questions": [
            {
                "key": "status",
                "label": "Recreational substance use status",
                "mode": "dropdown",
                "options": ["Never", "Former", "Current"],
            },
            {
                "key": "types",
                "label": "Which substances (if any)",
                "mode": "text",
                "options": [],
            },
        ],
    },
    "Exercise / Physical Activity": {
        "icon": ft.Icons.FITNESS_CENTER,
        "questions": [
            {
                "key": "days_per_week",
                "label": "Days per week you exercise",
                "mode": "dropdown",
                "options": ["0", "1-2", "3-4", "5-6", "Daily"],
            },
            {
                "key": "duration",
                "label": "Typical session length",
                "mode": "dropdown",
                "options": [
                    "Less than 15 minutes",
                    "15-30 minutes",
                    "30-60 minutes",
                    "More than 60 minutes",
                ],
            },
            {
                "key": "types",
                "label": "Primary activity type(s) (select all that apply)",
                "mode": "multi",
                "options": [
                    "Walking",
                    "Running / jogging",
                    "Swimming",
                    "Cycling",
                    "Strength training",
                    "Yoga / stretching",
                    "Team sports",
                    "Other",
                ],
            },
        ],
    },
    "Diet": {
        "icon": ft.Icons.RESTAURANT,
        "questions": [
            {
                "key": "restrictions",
                "label": "Dietary restrictions (select all that apply)",
                "mode": "multi",
                "options": [
                    "No restrictions",
                    "Vegetarian",
                    "Vegan",
                    "Gluten-free",
                    "Kosher",
                    "Halal",
                    "Low-sodium",
                    "Diabetic diet",
                    "Other",
                ],
            },
        ],
    },
    "Occupation": {
        "icon": ft.Icons.WORK,
        "questions": [
            {
                "key": "current",
                "label": "Current occupation",
                "mode": "text",
                "options": [],
            },
            {
                "key": "exposures",
                "label": "Workplace hazard exposure(s) (select all that apply)",
                "mode": "multi",
                "options": [
                    "None",
                    "Chemicals / solvents",
                    "Dust / asbestos",
                    "Radiation",
                    "Loud noise",
                    "Physical strain / repetitive motion",
                    "Biological hazards",
                    "Other",
                ],
            },
        ],
    },
}


def build_social_history(page: ft.Page) -> ft.Control:
    """Build the Social History questionnaire panel with dropdowns and checkboxes."""
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    s = pt_scale(page, 1)

    # Load existing data
    existing = _load_social(page, patient_id)
    by_cat: dict[str, dict] = {}
    for item in existing:
        cat = item.get("category", "")
        if cat:
            by_cat[cat] = item

    category_sections: list[ft.Control] = []

    for cat_name, cat_def in QUESTIONNAIRES.items():
        icon = cat_def["icon"]
        questions = cat_def["questions"]
        current = by_cat.get(cat_name, {})
        answers = current.get("answers", {})

        # Track controls for saving
        _dropdowns: dict[str, ft.Dropdown] = {}
        _checkboxes: dict[str, list[ft.Checkbox]] = {}
        _text_fields: dict[str, ft.TextField] = {}
        q_controls: list[ft.Control] = []

        for q in questions:
            q_key = q["key"]
            q_label = q["label"]
            options = q["options"]
            mode = q.get("mode", "dropdown")
            saved_val = answers.get(q_key, "")
            info_text = q.get("info")

            if mode == "dropdown":
                dd = ft.Dropdown(
                    label=q_label,
                    options=[ft.dropdown.Option(o) for o in options],
                    value=saved_val if saved_val in options else None,
                    expand=True,
                    dense=True,
                )
                _dropdowns[q_key] = dd

                if info_text:
                    def _show_info(e, title=q_label, body=info_text):
                        attr = "_sh_drink_info_dlg"
                        if not hasattr(page.mrma, attr):
                            def _close(e):
                                getattr(page.mrma, attr).open = False
                                page.update()
                            dlg = ft.AlertDialog(
                                modal=False,
                                title=ft.Text(title, size=14, weight="bold"),
                                content=ft.Text(body, size=13),
                                actions=[ft.TextButton("Close", on_click=_close)],
                            )
                            setattr(page.mrma, attr, dlg)
                            append_dialog(page, dlg)
                        getattr(page.mrma, attr).open = True
                        page.update()

                    q_controls.append(ft.Row([
                        dd,
                        ft.IconButton(
                            icon=ft.Icons.HELP_OUTLINE,
                            icon_size=18 * s,
                            icon_color=ft.Colors.GREY_500,
                            tooltip="What counts as one drink?",
                            on_click=_show_info,
                        ),
                    ], spacing=4))
                else:
                    q_controls.append(dd)

            elif mode == "multi":
                # Multi-select checkboxes
                # saved_val is a comma-separated string
                saved_list = [v.strip() for v in saved_val.split(",")
                              if v.strip()] if saved_val else []
                cbs: list[ft.Checkbox] = []
                for opt in options:
                    cb = ft.Checkbox(
                        label=opt,
                        value=opt in saved_list,
                    )
                    cbs.append(cb)
                _checkboxes[q_key] = cbs
                q_controls.append(
                    ft.Column([
                        ft.Text(q_label, size=13 * s, weight="w500"),
                        ft.Container(
                            content=ft.Column(cbs, spacing=2),
                            padding=ft.padding.only(left=20 * s),
                        ),
                    ], spacing=4)
                )

            else:  # text
                tf = ft.TextField(
                    label=q_label,
                    value=saved_val,
                    expand=True,
                    dense=True,
                )
                _text_fields[q_key] = tf
                q_controls.append(tf)

        # Notes field for every category
        notes_val = current.get("notes", "")
        notes_tf = ft.TextField(
            label="Additional notes",
            value=notes_val,
            dense=True,
            multiline=True,
            min_lines=1,
            max_lines=3,
            expand=True,
        )

        def _make_save(
            cat=cat_name,
            dds=_dropdowns,
            cbs_map=_checkboxes,
            tfs=_text_fields,
            ntf=notes_tf,
        ):
            def _save(_=None):
                items = _load_social(page, patient_id)
                items = [i for i in items if i.get("category") != cat]
                ans: dict[str, str] = {}
                for key, dd in dds.items():
                    if dd.value:
                        ans[key] = dd.value
                for key, cb_list in cbs_map.items():
                    checked = [cb.label for cb in cb_list if cb.value]
                    if checked:
                        ans[key] = ", ".join(checked)
                for key, tf in tfs.items():
                    val = (tf.value or "").strip()
                    if val:
                        ans[key] = val
                notes = (ntf.value or "").strip()
                if ans or notes:
                    items.append({
                        "category": cat,
                        "answers": ans,
                        "notes": notes,
                    })
                _save_social(page, patient_id, items)
            return _save

        save_fn = _make_save()

        # Auto-save on every control change (silent - no snack bar)
        for dd in _dropdowns.values():
            dd.on_change = lambda e, fn=save_fn: fn()
        for cb_list in _checkboxes.values():
            for cb in cb_list:
                cb.on_change = lambda e, fn=save_fn: fn()
        for tf in _text_fields.values():
            tf.on_blur = lambda e, fn=save_fn: fn()
        notes_tf.on_blur = lambda e, fn=save_fn: fn()

        section = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icon, size=18 * s, color=ft.Colors.TEAL_400),
                    ft.Text(cat_name, size=14 * s, weight="bold"),
                ], spacing=8),
                *q_controls,
                notes_tf,
            ], spacing=8 * s),
            padding=ft.padding.all(12 * s),
            border_radius=8 * s,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.border.all(1, OUTLINE_VARIANT),
        )
        category_sections.append(section)

    return ft.Column(category_sections, spacing=10 * s)

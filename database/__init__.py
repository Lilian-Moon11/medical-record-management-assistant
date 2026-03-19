# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Database package public interface (facade).
#
# Re-exports selected functions from internal submodules (core, patient,
# clinical) to provide a clean, centralized import surface for the app.
#
# NOTE: resource_path() has been removed -- path resolution is now in
# core/paths.py (platformdirs-based).
# -----------------------------------------------------------------------------

from .core import (
    open_or_create_vault,
    open_vault_with_recovery,
    get_setting,
    set_setting,
)
from .patient import (
    get_profile,
    create_profile,
    update_profile,
    list_field_definitions,
    ensure_field_definition,
    get_patient_field_map,
    upsert_patient_field_value,
    field_definition_exists,
    delete_field_definition,
    update_field_definition_label,
    update_field_definition_sensitivity,
    list_distinct_field_categories,
)
from .clinical import (
    list_providers, create_provider, update_provider, delete_provider,
    list_lab_reports, create_lab_report, update_lab_report, delete_lab_report,
    list_lab_results_for_report, add_lab_result, update_lab_result, delete_lab_result,
    list_distinct_test_names, list_all_results_for_test,
    get_patient_documents, add_document, delete_document,
    get_document_metadata, get_document_metadata as get_document_path,
)

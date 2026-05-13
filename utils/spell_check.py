# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Lightweight spell checking utility for user-typed text in notes and
# free-text fields. Uses pyspellchecker with a supplemental medical
# terminology dictionary so clinical terms aren't flagged as misspelled.
#
# Design:
#   - Singleton SpellChecker instance, lazy-initialized on first use
#   - Medical terms loaded once from a bundled word list
#   - Patient-specific terms (medication names, conditions) can be added
#     dynamically so existing chart data isn't flagged
#   - Only checks "prose" fields (notes, reactions, symptoms) — NOT
#     structured data (medication names, dates, dosages)
#
# Public API:
#   check_text(text) → list of SpellingIssue
#   apply_corrections(text, corrections) → str
#   add_known_words(words) → None
#   PROSE_FIELDS → frozenset of column keys that should be spell-checked
# -----------------------------------------------------------------------------

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Column keys that should NOT be spell-checked because they contain
# structured data (dates, doses, units) rather than prose.
SKIP_SPELL_CHECK_FIELDS = frozenset([
    "date",
    "onset_date",
    "diagnosis_date",
    "dose",
    "unit",
    "value",
    "value_text",
    "is_current",
    "is_active",
    "member_id",
    "group_no",
    "bin",
    "pcn",
    "phone",
])


def should_check_field(field_key: str) -> bool:
    """Return True if this column key should be spell-checked.

    Checks everything EXCEPT structured data fields (dates, doses, etc.).
    """
    return field_key not in SKIP_SPELL_CHECK_FIELDS


# Minimum word length to check (skip "a", "or", abbreviations)
_MIN_WORD_LEN = 3


@dataclass
class SpellingIssue:
    """A single misspelled word with its correction suggestions."""
    word: str
    suggestions: list[str]
    # Position info for potential future use
    field_key: str = ""


# ---------------------------------------------------------------------------
# Singleton spell checker
# ---------------------------------------------------------------------------
_spell = None

# Common medical terms that pyspellchecker's English dictionary won't have.
# This is a curated starter list — the dynamic loader adds patient-specific
# terms (medication names, conditions) at runtime.
_MEDICAL_TERMS = [
    # Body systems / anatomy
    "abdominal", "abdomen", "adrenal", "arterial", "articular", "bilateral",
    "brachial", "bronchial", "cardiac", "cardiovascular", "carotid", "celiac",
    "cervical", "cranial", "dermal", "diaphragm", "duodenal", "endocrine",
    "epidural", "esophageal", "femoral", "gastrointestinal", "hepatic",
    "inguinal", "intracranial", "lumbar", "meningeal", "musculoskeletal",
    "nasopharyngeal", "neurological", "occipital", "ophthalmic", "otic",
    "pancreatic", "pelvic", "perineal", "peritoneal", "pharyngeal", "pleural",
    "pulmonary", "renal", "sacral", "spinal", "subcutaneous", "sublingual",
    "thoracic", "thyroid", "tracheal", "urethral", "urinary", "uterine",
    "vaginal", "vascular", "ventricular",

    # Common conditions
    "anemia", "arrhythmia", "arthritis", "asthma", "bradycardia",
    "bronchitis", "bursitis", "cellulitis", "cholesterol", "colitis",
    "copd", "cystitis", "dermatitis", "diverticulitis", "dyslipidemia",
    "dyspnea", "eczema", "edema", "embolism", "emphysema", "endometriosis",
    "fibrillation", "fibromyalgia", "gastritis", "glaucoma", "gout",
    "hemorrhoid", "hepatitis", "hernia", "hypertension", "hyperthyroidism",
    "hypoglycemia", "hypothyroidism", "incontinence", "ischemia",
    "lymphedema", "meningitis", "migraine", "nausea", "neuropathy",
    "osteoarthritis", "osteoporosis", "pancreatitis", "pharyngitis",
    "pleurisy", "pneumonia", "polyp", "prediabetes", "psoriasis",
    "rhinitis", "sciatica", "scoliosis", "sepsis", "sinusitis",
    "stenosis", "tachycardia", "tendinitis", "thrombosis", "tinnitus",
    "vertigo",

    # Procedures / tests
    "angiogram", "angioplasty", "appendectomy", "arthroscopy", "biopsy",
    "catheterization", "cholecystectomy", "colonoscopy", "colposcopy",
    "cryosurgery", "cystoscopy", "defibrillator", "dialysis", "echocardiogram",
    "electrocardiogram", "endoscopy", "epidural", "hysterectomy",
    "laparoscopy", "laparotomy", "lumpectomy", "mammogram", "mastectomy",
    "mri", "pacemaker", "radiograph", "sigmoidoscopy", "sonogram",
    "spirometry", "stent", "tonsillectomy", "tracheostomy", "ultrasound",
    "urinalysis", "vasectomy",

    # Medication forms / routes
    "capsule", "capsules", "dosage", "elixir", "inhaler", "injectable",
    "inhalation", "intramuscular", "intravenous", "lozenge", "milligram",
    "milligrams", "microgram", "micrograms", "nebulizer", "ointment",
    "ophthalmic", "suppository", "sublingual", "subcutaneous", "suspension",
    "syringe", "tablet", "tablets", "topical", "transdermal",

    # Common drug name stems / generics
    "acetaminophen", "albuterol", "amlodipine", "amoxicillin", "aspirin",
    "atenolol", "atorvastatin", "azithromycin", "benzodiazepine",
    "budesonide", "bupropion", "carvedilol", "cephalexin", "cetirizine",
    "ciprofloxacin", "citalopram", "clindamycin", "clonazepam",
    "cyclobenzaprine", "diazepam", "diclofenac", "diltiazem", "doxycycline",
    "duloxetine", "escitalopram", "estradiol", "famotidine", "fluoxetine",
    "fluticasone", "furosemide", "gabapentin", "hydrochlorothiazide",
    "hydrocodone", "hydroxychloroquine", "ibuprofen", "insulin",
    "lamotrigine", "levothyroxine", "lisinopril", "loratadine", "lorazepam",
    "losartan", "meloxicam", "metformin", "methotrexate", "metoprolol",
    "metronidazole", "montelukast", "naproxen", "norethindrone",
    "omeprazole", "ondansetron", "oxycodone", "pantoprazole", "paroxetine",
    "penicillin", "prednisone", "pregabalin", "propranolol", "quetiapine",
    "rosuvastatin", "sertraline", "simvastatin", "spironolactone",
    "sumatriptan", "tamsulosin", "tramadol", "trazodone", "valacyclovir",
    "venlafaxine", "warfarin", "zolpidem",

    # Clinical terms
    "analgesic", "antibiotic", "anticoagulant", "antidepressant",
    "antihistamine", "antihypertensive", "antipyretic", "antiviral",
    "benign", "biomarker", "comorbidity", "contraindication", "etiology",
    "exacerbation", "idiopathic", "malignant", "metastasis", "palliative",
    "pathology", "pharmacology", "prognosis", "prophylaxis", "remission",
    "symptomatic", "systemic",

    # Lab / vital terms
    "cholesterol", "creatinine", "glucose", "hematocrit", "hemoglobin",
    "leukocyte", "lipid", "platelet", "potassium", "sodium", "triglyceride",
    "systolic", "diastolic", "saturation", "bmi",
]


def _get_spell():
    """Lazy-initialize the spell checker singleton."""
    global _spell
    if _spell is not None:
        return _spell

    try:
        from spellchecker import SpellChecker
        _spell = SpellChecker()
        # Load medical terms
        _spell.word_frequency.load_words(_MEDICAL_TERMS)
        logger.debug("spell_check: initialized with %d medical terms", len(_MEDICAL_TERMS))
    except ImportError:
        logger.warning("spell_check: pyspellchecker not installed, spell checking disabled")
        _spell = None
    except Exception as exc:
        logger.warning("spell_check: failed to initialize: %s", exc)
        _spell = None

    return _spell


def add_known_words(words: list[str]) -> None:
    """Add patient-specific words (medication names, conditions) to the dictionary.

    Call this when a patient's chart is loaded so their existing data
    isn't flagged as misspelled.
    """
    spell = _get_spell()
    if spell is None:
        return
    # Normalize and filter
    clean = [w.strip().lower() for w in words if w and len(w.strip()) >= _MIN_WORD_LEN]
    if clean:
        spell.word_frequency.load_words(clean)
        logger.debug("spell_check: added %d patient-specific words", len(clean))


def check_text(text: str, field_key: str = "") -> list[SpellingIssue]:
    """Check a text string for misspelled words.

    Returns a list of SpellingIssue objects for each misspelled word found.
    Returns an empty list if spell checking is unavailable or the text is clean.
    """
    spell = _get_spell()
    if spell is None or not text:
        return []

    # Extract words (letters and apostrophes only, skip numbers/doses)
    words = re.findall(r"[a-zA-Z']+", text)
    # Filter: skip short words, all-caps abbreviations, and words with numbers
    candidates = [
        w for w in words
        if len(w) >= _MIN_WORD_LEN and not w.isupper()
    ]

    if not candidates:
        return []

    misspelled = spell.unknown(candidates)
    issues = []
    for word in misspelled:
        # Get up to 3 suggestions
        corrections = spell.candidates(word)
        suggestions = sorted(corrections, key=lambda c: spell.word_frequency[c], reverse=True)[:3] if corrections else []
        issues.append(SpellingIssue(
            word=word,
            suggestions=suggestions,
            field_key=field_key,
        ))

    return issues


def apply_corrections(text: str, corrections: dict[str, str]) -> str:
    """Apply a dict of {misspelled_word: correction} to the text.

    Case-preserving: if the original was capitalized, the correction
    will be too.
    """
    if not corrections or not text:
        return text

    for wrong, right in corrections.items():
        # Case-preserving replacement
        def _replace(match):
            original = match.group(0)
            if original[0].isupper():
                return right.capitalize()
            return right

        pattern = re.compile(re.escape(wrong), re.IGNORECASE)
        text = pattern.sub(_replace, text)

    return text

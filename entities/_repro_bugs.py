"""Minimal repro for entity-extraction false positives found in the
Relation Therapeutics /ingest-company run (2026-08-03).

Run before the fix: bad cases print STILL BROKEN.
Run after the fix: bad cases print FIXED, regression cases stay OK.
"""
from entities.dictionaries import load_dictionaries
from entities.extractor import extract_entities

dicts = load_dictionaries()

cases = [
    ("multi-modal patient data", "companies", "Modal"),
    ("Owkin's COMPOTES/MOSAIC approach for patient target discovery in oncology",
     "diseases", "mosaic"),
    ("GSK deal covers preclinical and clinical development milestones per target; "
     "$45M upfront (incl. $15M equity) plus royalties",
     "diseases", "infantile neuronal ceroid lipofuscinosis"),
    ("Relation Therapeutics is not an ADC company", "methods", "ADC"),
]

print("--- bug cases (should be FIXED after patch) ---")
for text, etype, bad in cases:
    result = extract_entities(text, dicts)
    names = [m.canonical for m in getattr(result, etype)]
    status = "STILL BROKEN" if bad in names else "FIXED"
    print(f"[{status}] {etype}={names!r}  text={text!r}")

print("\n--- regression guard (must stay OK after patch) ---")
regression_cases = [
    ("KRAS-mutant NSCLC responded to treatment", "genes", "KRAS"),
    ("a CRISPR-based screening approach", "methods", "CRISPR"),
]
for text, etype, good in regression_cases:
    result = extract_entities(text, dicts)
    names = [m.canonical for m in getattr(result, etype)]
    status = "OK" if good in names else "REGRESSION"
    print(f"[{status}] {etype}={names!r}  text={text!r}")

"""Load and cache entity dictionaries from ontology files."""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / 'entity_data'

_cached_dictionaries = None


@dataclass
class EntityDictionaries:
    gene_symbols: set[str] = field(default_factory=set)
    gene_aliases: dict[str, str] = field(default_factory=dict)
    gene_info: dict[str, dict] = field(default_factory=dict)

    disease_names: dict[str, dict] = field(default_factory=dict)

    # compounds: canonical_name -> {target, class, ...} metadata
    compounds: dict[str, dict] = field(default_factory=dict)
    # compound_codes: M-code (uppercase) -> canonical_name
    compound_codes: dict[str, str] = field(default_factory=dict)

    # company_variants: variant_name (lowercase) -> canonical_name
    company_variants: dict[str, str] = field(default_factory=dict)
    # company_parents: canonical_name -> parent canonical_name
    company_parents: dict[str, str] = field(default_factory=dict)

    # drug_variants: variant_name (lowercase) -> canonical_name
    drug_variants: dict[str, str] = field(default_factory=dict)

    # method_variants: variant_name (lowercase) -> canonical_name
    method_variants: dict[str, str] = field(default_factory=dict)

    # clinical_trial_variants: variant_name (lowercase) -> canonical_name
    clinical_trial_variants: dict[str, str] = field(default_factory=dict)

    # cell_line_variants: variant_name (lowercase) -> canonical_name
    cell_line_variants: dict[str, str] = field(default_factory=dict)

    # conference_variants: variant_name (lowercase) -> canonical_name
    conference_variants: dict[str, str] = field(default_factory=dict)

    # pathway_variants: variant_name (lowercase) -> canonical_name
    pathway_variants: dict[str, str] = field(default_factory=dict)

    # department_variants: variant_name (lowercase) -> canonical_name
    department_variants: dict[str, str] = field(default_factory=dict)

    # Short gene symbols (<=3 chars) that need context validation
    short_gene_symbols: set[str] = field(default_factory=set)


def dictionaries_available() -> bool:
    """Return True if the main ontology JSON files have been built."""
    return (DATA_DIR / 'hgnc_genes.json').exists() and (DATA_DIR / 'mondo_diseases.json').exists()


def load_dictionaries() -> EntityDictionaries:
    """Load all entity dictionaries. Cached after first call."""
    global _cached_dictionaries
    if _cached_dictionaries is not None:
        return _cached_dictionaries

    dicts = EntityDictionaries()

    # Load HGNC genes
    hgnc_path = DATA_DIR / 'hgnc_genes.json'
    if hgnc_path.exists():
        with open(hgnc_path, encoding='utf-8') as f:
            data = json.load(f)

        # Validate structure and minimum expected size
        if not isinstance(data, dict):
            raise ValueError(f"HGNC genes file has invalid structure: expected dict, got {type(data)}")

        genes = data.get('genes', {})
        if not isinstance(genes, dict):
            raise ValueError(f"HGNC 'genes' has invalid structure: expected dict, got {type(genes)}")

        # HGNC has ~20,000+ genes - if we have significantly fewer, file may be corrupted
        if len(genes) < 1000:
            raise ValueError(f"HGNC genes file appears corrupted: only {len(genes)} genes found (expected 20,000+)")

        dicts.gene_symbols = set(genes.keys())
        dicts.gene_info = genes
        dicts.gene_aliases = data.get('alias_map', {})
        dicts.short_gene_symbols = {s for s in dicts.gene_symbols if len(s) <= 3}

        logger.debug(f"Loaded {len(dicts.gene_symbols)} gene symbols, "
                     f"{len(dicts.gene_aliases)} aliases")
    else:
        logger.warning(f"HGNC genes file not found: {hgnc_path}")

    # Load MONDO diseases
    mondo_path = DATA_DIR / 'mondo_diseases.json'
    if mondo_path.exists():
        with open(mondo_path, encoding='utf-8') as f:
            diseases = json.load(f)

        # Validate structure and minimum expected size
        if not isinstance(diseases, dict):
            raise ValueError(f"MONDO diseases file has invalid structure: expected dict, got {type(diseases)}")

        # MONDO has ~20,000+ disease entries - if we have significantly fewer, file may be corrupted
        if len(diseases) < 1000:
            raise ValueError(f"MONDO diseases file appears corrupted: only {len(diseases)} entries found (expected 20,000+)")

        dicts.disease_names = diseases
        logger.debug(f"Loaded {len(dicts.disease_names)} disease entries")
    else:
        logger.warning(f"MONDO diseases file not found: {mondo_path}")

    # Load internal compounds
    compounds_path = DATA_DIR / 'internal_compounds.yaml'
    if compounds_path.exists():
        with open(compounds_path, encoding='utf-8') as f:
            compounds_data = yaml.safe_load(f) or {}
        _mcode_re = re.compile(r'^m\d{4}$', re.IGNORECASE)
        for canonical, entry in compounds_data.items():
            entry = entry or {}
            dicts.compounds[canonical] = entry
            for v in entry.get('variants', []):
                if _mcode_re.match(v):
                    dicts.compound_codes[v.upper()] = canonical
        logger.debug(f"Loaded {len(dicts.compounds)} internal compounds, "
                     f"{len(dicts.compound_codes)} M-codes")
    else:
        logger.warning(f"Internal compounds file not found: {compounds_path}")

    # Load drugs
    drugs_path = DATA_DIR / 'drugs.yaml'
    if drugs_path.exists():
        with open(drugs_path, encoding='utf-8') as f:
            drugs_data = yaml.safe_load(f) or {}
        for canonical, entry in drugs_data.items():
            dicts.drug_variants[canonical.lower()] = canonical
            if isinstance(entry, dict):
                for v in entry.get('variants', []):
                    dicts.drug_variants[v.lower()] = canonical
            elif isinstance(entry, list):
                for v in entry:
                    dicts.drug_variants[v.lower()] = canonical
        logger.debug(f"Loaded {len(dicts.drug_variants)} drug name variants")
    else:
        logger.warning(f"Drugs file not found: {drugs_path}")

    # Load companies
    companies_path = DATA_DIR / 'companies.yaml'
    if companies_path.exists():
        with open(companies_path, encoding='utf-8') as f:
            companies_data = yaml.safe_load(f) or {}
        for canonical, entry in companies_data.items():
            if isinstance(entry, dict):
                for v in entry.get('variants', []):
                    dicts.company_variants[v.lower()] = canonical
                parent = entry.get('parent')
                if parent:
                    dicts.company_parents[canonical] = parent
            elif isinstance(entry, list):
                for v in entry:
                    dicts.company_variants[v.lower()] = canonical
        logger.debug(f"Loaded {len(dicts.company_variants)} company name variants, "
                     f"{len(dicts.company_parents)} parent relationships")
    else:
        logger.warning(f"Companies file not found: {companies_path}")

    # Load methods/technologies
    methods_path = DATA_DIR / 'methods.yaml'
    if methods_path.exists():
        with open(methods_path, encoding='utf-8') as f:
            methods_data = yaml.safe_load(f) or {}
        for canonical, variants in methods_data.items():
            if isinstance(variants, list):
                for v in variants:
                    dicts.method_variants[v.lower()] = canonical
        logger.debug(f"Loaded {len(dicts.method_variants)} method name variants")

    # Load clinical trials
    trials_path = DATA_DIR / 'clinical_trials.yaml'
    if trials_path.exists():
        with open(trials_path, encoding='utf-8') as f:
            trials_data = yaml.safe_load(f) or {}
        for canonical, variants in trials_data.items():
            if isinstance(variants, list):
                for v in variants:
                    dicts.clinical_trial_variants[v.lower()] = canonical
        logger.debug(f"Loaded {len(dicts.clinical_trial_variants)} clinical trial variants")

    # Load cell lines
    cell_lines_path = DATA_DIR / 'cell_lines.yaml'
    if cell_lines_path.exists():
        with open(cell_lines_path, encoding='utf-8') as f:
            cell_lines_data = yaml.safe_load(f) or {}
        for canonical, variants in cell_lines_data.items():
            if isinstance(variants, list):
                for v in variants:
                    dicts.cell_line_variants[v.lower()] = canonical
        logger.debug(f"Loaded {len(dicts.cell_line_variants)} cell line variants")

    # Load conferences
    conferences_path = DATA_DIR / 'conferences.yaml'
    if conferences_path.exists():
        with open(conferences_path, encoding='utf-8') as f:
            conferences_data = yaml.safe_load(f) or {}
        for canonical, variants in conferences_data.items():
            if isinstance(variants, list):
                for v in variants:
                    dicts.conference_variants[v.lower()] = canonical
        logger.debug(f"Loaded {len(dicts.conference_variants)} conference variants")

    # Load pathways
    pathways_path = DATA_DIR / 'pathways.yaml'
    if pathways_path.exists():
        with open(pathways_path, encoding='utf-8') as f:
            pathways_data = yaml.safe_load(f) or {}
        for canonical, variants in pathways_data.items():
            if isinstance(variants, list):
                for v in variants:
                    dicts.pathway_variants[v.lower()] = canonical
        logger.debug(f"Loaded {len(dicts.pathway_variants)} pathway variants")

    # Load departments
    departments_path = DATA_DIR / 'departments.yaml'
    if departments_path.exists():
        with open(departments_path, encoding='utf-8') as f:
            departments_data = yaml.safe_load(f) or {}
        for canonical, variants in departments_data.items():
            if isinstance(variants, list):
                for v in variants:
                    dicts.department_variants[v.lower()] = canonical
        logger.debug(f"Loaded {len(dicts.department_variants)} department variants")

    _cached_dictionaries = dicts
    return dicts


def reload_dictionaries() -> EntityDictionaries:
    """Force reload of dictionaries (e.g., after updating YAML files)."""
    global _cached_dictionaries
    _cached_dictionaries = None

    # Clear derived caches that depend on dictionary data
    from entities.extractor import _reset_disease_word_index
    _reset_disease_word_index()

    return load_dictionaries()

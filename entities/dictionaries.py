"""Load and cache entity dictionaries from ontology files."""

import json
import logging
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

    compounds: dict[str, dict] = field(default_factory=dict)

    # company_variants: variant_name (lowercase) -> canonical_name
    company_variants: dict[str, str] = field(default_factory=dict)

    # method_variants: variant_name (lowercase) -> canonical_name
    method_variants: dict[str, str] = field(default_factory=dict)

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

        genes = data.get('genes', {})
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
            dicts.disease_names = json.load(f)
        logger.debug(f"Loaded {len(dicts.disease_names)} disease entries")
    else:
        logger.warning(f"MONDO diseases file not found: {mondo_path}")

    # Load internal compounds
    compounds_path = DATA_DIR / 'internal_compounds.yaml'
    if compounds_path.exists():
        with open(compounds_path, encoding='utf-8') as f:
            dicts.compounds = yaml.safe_load(f) or {}
        logger.debug(f"Loaded {len(dicts.compounds)} internal compounds")
    else:
        logger.warning(f"Internal compounds file not found: {compounds_path}")

    # Load companies
    companies_path = DATA_DIR / 'companies.yaml'
    if companies_path.exists():
        with open(companies_path, encoding='utf-8') as f:
            companies_data = yaml.safe_load(f) or {}
        for canonical, variants in companies_data.items():
            if isinstance(variants, list):
                for v in variants:
                    dicts.company_variants[v.lower()] = canonical
        logger.debug(f"Loaded {len(dicts.company_variants)} company name variants")
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

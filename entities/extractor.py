"""Tier 1 entity extraction: regex + dictionary matching against ontologies."""

import re
import logging
from dataclasses import dataclass, field

from entities.dictionaries import EntityDictionaries, load_dictionaries

logger = logging.getLogger(__name__)

COMPOUND_RE = re.compile(r'\bM(\d{4})\b')

# Context words that indicate biomedical content nearby (for short gene symbol validation)
BIOMEDICAL_CONTEXT_WORDS = {
    'cancer', 'tumor', 'tumour', 'mutation', 'inhibitor', 'antibody', 'receptor',
    'kinase', 'pathway', 'expression', 'biomarker', 'therapy', 'treatment',
    'clinical', 'trial', 'patient', 'oncology', 'cell', 'protein', 'gene',
    'target', 'drug', 'compound', 'adc', 'bispecific', 'degrader', 'nsclc',
    'crc', 'aml', 'melanoma', 'carcinoma', 'lymphoma', 'sarcoma', 'leukemia',
    'phosphorylation', 'signaling', 'apoptosis', 'proliferation', 'metastasis',
    'staining', 'ihc', 'immunohistochemistry', 'fluorescence', 'assay',
}

# Common English/German words that happen to be valid HGNC symbols — always skip
FALSE_POSITIVE_GENES = {
    'FOR', 'WAS', 'CAN', 'SET', 'MAN', 'SHE', 'HER', 'NOT', 'ALL', 'AND',
    'THE', 'HAS', 'HAD', 'ARE', 'ONE', 'TWO', 'SIX', 'TEN', 'AGE',
    'END', 'GAP', 'MAP', 'CAP', 'CAB', 'GAS', 'REST', 'FAST', 'IMPACT',
    'FATE', 'CAMP', 'CAST', 'COPE', 'CARD', 'CARE', 'CHANCE', 'CHARGE',
    'CLASS', 'CLOCK', 'COIL', 'COMPLEX', 'CORD', 'CORE', 'COUNT', 'CUT',
    'DAM', 'DASH', 'DOCK', 'DOME', 'DRAW', 'DROP', 'FACE', 'FACT', 'FAN',
    'FIG', 'FIT', 'FIST', 'FLAG', 'FLAP', 'FLIP', 'FOLD', 'FORK',
    'HAND', 'HEAT', 'HELP', 'HINT', 'HOOK', 'HUNT', 'LACK', 'LAMP',
    'LARD', 'LAST', 'LEAD', 'LENS', 'LOCK', 'MARCH', 'MARK', 'MASK',
    'MAST', 'MATCH', 'MICE', 'MINT', 'MOST', 'MOVE', 'NANS', 'NEST',
    'PACE', 'PALM', 'PARK', 'PART', 'PEAK', 'PICK', 'PIN', 'PIPE',
    'POLE', 'POLL', 'POOL', 'PORT', 'PROM', 'RACK', 'RAMP', 'RING',
    'ROCK', 'ROLL', 'ROOF', 'SASH', 'SEAL', 'SEED', 'SHIP', 'SLAB',
    'SLAM', 'SLAP', 'SLIM', 'SLIP', 'SLUG', 'SNAP', 'SORE', 'SORT',
    'SPAN', 'SPAR', 'SPEC', 'SPIN', 'SPIT', 'SPOT', 'STAR', 'STEM',
    'STEP', 'STING', 'STOP', 'STUB', 'SURF', 'SWAP', 'TANK', 'TARP',
    'TIDE', 'TRAM', 'TRAP', 'TRIM', 'TRIP', 'TUBE', 'TUNA', 'VENT',
    'VOLT', 'WASP', 'WRAP',
    'DES', 'ICH', 'MIT', 'AUS', 'BEI', 'GUT', 'ORT', 'RAD', 'TAG', 'WEG',
    'ADC', 'PDX', 'PC', 'NA', 'NAC', 'OR', 'HR', 'CI', 'OS', 'PFS', 'ORR',
    'CR', 'PR', 'SD', 'PD', 'AE', 'SAE', 'DLT', 'MTD', 'RP2D', 'BID',
    'QD', 'IV', 'SC', 'PO', 'IM', 'IT', 'IP', 'QC', 'APOLLO',
    'ACE', 'BIN', 'ADD', 'BIG', 'BIT', 'BOX', 'BUS', 'CAR', 'CAT', 'COG',
    'COW', 'CRY', 'CUP', 'DAD', 'DAY', 'DIG', 'DIM', 'DOG', 'DOT', 'DRY',
    'DUG', 'EAR', 'EAT', 'EGG', 'ERA', 'EVE', 'EYE', 'FAD', 'FAT', 'FEW',
    'FIN', 'FLY', 'FOG', 'FOX', 'FUN', 'FUR', 'GOD', 'GUM', 'GUN',
    'GYM', 'HAM', 'HAT', 'HEN', 'HEX', 'HID', 'HIT', 'HOG', 'HOP', 'HOT',
    'HUB', 'HUG', 'HUT', 'ICE', 'ILL', 'INK', 'INN', 'ION', 'IVY', 'JAM',
    'JAR', 'JAW', 'JET', 'JOB', 'JOG', 'JOY', 'JUG', 'KIT', 'LAB', 'LAP',
    'LAW', 'LAY', 'LEG', 'LET', 'LID', 'LIP', 'LOG', 'LOT', 'LOW', 'MAD',
    'MAT', 'MAX', 'MIX', 'MOB', 'MOM', 'MOP', 'MUD', 'MUG', 'NAP', 'NET',
    'NEW', 'NIT', 'NOD', 'NOR', 'NUN', 'NUT', 'OAK', 'OAT', 'ODD', 'OIL',
    'OLD', 'OPT', 'ORB', 'ORE', 'OUR', 'OUT', 'OWE', 'OWL', 'OWN', 'PAD',
    'PAN', 'PAT', 'PAW', 'PAY', 'PEA', 'PEG', 'PEN', 'PET', 'PIE', 'PIG',
    'PIT', 'PLY', 'POD', 'POP', 'POT', 'POW', 'PRY', 'PUB', 'PUG', 'PUN',
    'PUS', 'PUT', 'RAG', 'RAM', 'RAN', 'RAP', 'RAT', 'RAW', 'RAY', 'RED',
    'RIB', 'RID', 'RIG', 'RIM', 'RIP', 'ROB', 'ROD', 'ROT', 'ROW', 'RUB',
    'RUG', 'RUN', 'RUT', 'RYE', 'SAD', 'SAG', 'SAP', 'SAT', 'SAW', 'SAY',
    'SEA', 'SEW', 'SHY', 'SIN', 'SIP', 'SIS', 'SIT', 'SKI', 'SKY', 'SLY',
    'SOB', 'SOD', 'SON', 'SOP', 'SOT', 'SOW', 'SOY', 'SPA', 'SPY', 'STY',
    'SUB', 'SUM', 'SUN', 'SUP', 'TAB', 'TAN', 'TAP', 'TAR', 'TAT', 'TAX',
    'TEA', 'THE', 'TIE', 'TIN', 'TIP', 'TOE', 'TON', 'TOO', 'TOP', 'TOW',
    'TOY', 'TRY', 'TUB', 'TUG', 'URN', 'USE', 'VAN', 'VAT', 'VET', 'VIA',
    'VIE', 'VOW', 'WAD', 'WAR', 'WAX', 'WEB', 'WED', 'WET', 'WHO', 'WIG',
    'WIN', 'WIT', 'WOE', 'WOK', 'WON', 'WOO', 'WOW', 'YAM', 'YAP', 'YAW',
    'YEW', 'YIN', 'ZAP', 'ZEN', 'ZIP', 'ZIT', 'ZOO',
}

# Well-known gene symbols that should always be recognized
ALWAYS_VALID_GENES = {
    'ALK', 'MET', 'RET', 'ROS1', 'AKT', 'AKT1', 'AKT2', 'AKT3',
    'RAF', 'RAS', 'SRC', 'ABL', 'ABL1', 'BCL2', 'BCR', 'FOS', 'JUN',
    'MYC', 'RB1', 'APC', 'VHL', 'NF1', 'NF2', 'EZH2', 'IDH1', 'IDH2',
    'FLT3', 'KIT', 'JAK2', 'MPL', 'CSF1R',
}


@dataclass
class EntityMention:
    text: str
    canonical: str
    entity_type: str  # gene, drug, disease, compound
    ontology_id: str = ""
    confidence: float = 1.0


@dataclass
class EntityResult:
    genes: list[EntityMention] = field(default_factory=list)
    drugs: list[EntityMention] = field(default_factory=list)
    diseases: list[EntityMention] = field(default_factory=list)
    compounds: list[EntityMention] = field(default_factory=list)
    companies: list[EntityMention] = field(default_factory=list)
    roles: list[EntityMention] = field(default_factory=list)
    methods: list[EntityMention] = field(default_factory=list)

    ENTITY_TYPES = ('genes', 'drugs', 'diseases', 'compounds', 'companies', 'roles', 'methods')

    def is_empty(self) -> bool:
        return not any(getattr(self, t) for t in self.ENTITY_TYPES)

    def merge(self, other: 'EntityResult'):
        """Merge another EntityResult, deduplicating by canonical name."""
        for entity_type in self.ENTITY_TYPES:
            existing = {m.canonical for m in getattr(self, entity_type)}
            for m in getattr(other, entity_type):
                if m.canonical not in existing:
                    getattr(self, entity_type).append(m)
                    existing.add(m.canonical)

    def to_dict(self) -> dict:
        """Serialize for YAML frontmatter and caching."""
        result = {}
        for entity_type in self.ENTITY_TYPES:
            mentions = getattr(self, entity_type)
            if mentions:
                result[entity_type] = sorted(set(m.canonical for m in mentions))
        return result


def _has_biomedical_context(text: str, pos: int, window: int = 100) -> bool:
    """Check if there are biomedical context words near the given position."""
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    snippet = text[start:end].lower()
    return any(word in snippet for word in BIOMEDICAL_CONTEXT_WORDS)


def _extract_compounds(text: str, dicts: EntityDictionaries) -> list[EntityMention]:
    """Extract M#### internal compound codes."""
    mentions = []
    seen = set()
    for match in COMPOUND_RE.finditer(text):
        code = f"M{match.group(1)}"
        if code in seen:
            continue
        seen.add(code)

        mentions.append(EntityMention(
            text=match.group(0),
            canonical=code,
            entity_type='compound',
            ontology_id='',
            confidence=1.0,
        ))
    return mentions


def _extract_genes(text: str, dicts: EntityDictionaries) -> list[EntityMention]:
    """Extract gene/protein symbols using HGNC dictionary."""
    mentions = []
    seen = set()

    # Find uppercase word tokens that could be gene symbols
    # Allow internal hyphens (PD-L1) but not trailing (KRAS-mutant)
    for match in re.finditer(r'\b([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)*)\b', text):
        candidate = match.group(1)
        if candidate in seen:
            continue

        # Skip known false positives
        if candidate in FALSE_POSITIVE_GENES:
            continue

        # Check against HGNC
        canonical = candidate
        hgnc_id = ''
        is_alias = False

        if candidate in dicts.gene_symbols:
            info = dicts.gene_info.get(candidate, {})
            hgnc_id = info.get('hgnc_id', '')
        elif candidate in dicts.gene_aliases:
            canonical = dicts.gene_aliases[candidate]
            info = dicts.gene_info.get(canonical, {})
            hgnc_id = info.get('hgnc_id', '')
            is_alias = True
        else:
            continue

        # Short symbols (<=3 chars) or short aliases need stricter validation
        needs_context = (
            (len(candidate) <= 3 and candidate not in ALWAYS_VALID_GENES)
            or (is_alias and len(candidate) <= 4)
        )
        if needs_context:
            if not _has_biomedical_context(text, match.start()):
                continue

        seen.add(candidate)
        if canonical != candidate:
            seen.add(canonical)

        # When matched via alias, show as "ALIAS (CANONICAL)" to preserve familiar names
        if is_alias and candidate != canonical:
            display = f"{candidate} ({canonical})"
        else:
            display = canonical

        mentions.append(EntityMention(
            text=candidate,
            canonical=display,
            entity_type='gene',
            ontology_id=hgnc_id,
        ))

    return mentions


_disease_word_index: dict[str, list[str]] | None = None


def _reset_disease_word_index():
    """Clear cached disease word index (called on dictionary reload)."""
    global _disease_word_index
    _disease_word_index = None


def _get_disease_word_index(dicts: EntityDictionaries) -> dict[str, list[str]]:
    """Build an inverted index: word -> [disease_keys containing that word].

    This makes disease lookup O(words_in_text) instead of O(diseases_in_ontology).
    """
    global _disease_word_index
    if _disease_word_index is not None:
        return _disease_word_index

    index: dict[str, list[str]] = {}
    for disease_key in dicts.disease_names:
        if len(disease_key) < 4:
            continue
        # Index by the longest word in the disease name (most selective)
        words = re.findall(r'[a-z]{4,}', disease_key)
        if words:
            longest = max(words, key=len)
            index.setdefault(longest, []).append(disease_key)

    _disease_word_index = index
    return index


# Overly generic disease terms to exclude
_GENERIC_DISEASE_TERMS = {
    'disease', 'syndrome', 'disorder', 'neoplasm', 'infection', 'cancer',
    'tumor', 'tumour', 'deficiency', 'malformation',
}


def _extract_diseases(text: str, dicts: EntityDictionaries) -> list[EntityMention]:
    """Extract disease names using MONDO dictionary with inverted word index."""
    if not dicts.disease_names:
        return []

    mentions = []
    seen = set()
    text_lower = text.lower()

    # Get the inverted index and find candidate diseases
    word_index = _get_disease_word_index(dicts)
    text_words = set(re.findall(r'[a-z]{4,}', text_lower))

    candidates = set()
    for word in text_words:
        if word in word_index:
            candidates.update(word_index[word])

    for disease_key in candidates:
        if disease_key in _GENERIC_DISEASE_TERMS:
            continue

        # Fast substring check before expensive regex
        pos = text_lower.find(disease_key)
        if pos == -1:
            continue
        # Verify word boundaries
        if pos > 0 and text_lower[pos - 1].isalnum():
            continue
        end = pos + len(disease_key)
        if end < len(text_lower) and text_lower[end].isalnum():
            continue

        info = dicts.disease_names[disease_key]
        canonical = info.get('label', disease_key)
        canonical_lower = canonical.lower()

        if canonical_lower in _GENERIC_DISEASE_TERMS:
            continue

        if canonical_lower in seen:
            continue
        seen.add(canonical_lower)

        mentions.append(EntityMention(
            text=disease_key,
            canonical=canonical,
            entity_type='disease',
            ontology_id=info.get('mondo_id', ''),
        ))

    return mentions


def _extract_from_variants(text_lower: str, variants: dict[str, str],
                           entity_type: str, min_len: int = 3) -> list[EntityMention]:
    """Extract entities by matching variant spellings against text."""
    mentions = []
    seen = set()

    for variant_lower, canonical in variants.items():
        if len(variant_lower) < min_len:
            continue
        pattern = r'\b' + re.escape(variant_lower) + r'\b'
        if re.search(pattern, text_lower):
            if canonical in seen:
                continue
            seen.add(canonical)
            mentions.append(EntityMention(
                text=variant_lower,
                canonical=canonical,
                entity_type=entity_type,
            ))

    return mentions


def _extract_companies(text: str, dicts: EntityDictionaries) -> list[EntityMention]:
    """Extract company names using curated dictionary."""
    if not dicts.company_variants:
        return []
    return _extract_from_variants(text.lower(), dicts.company_variants, 'company', min_len=3)


ROLE_TITLES = [
    'Executive Director', 'Associate Director', 'Senior Director',
    'Principal Scientist', 'Senior Scientist', 'Research Associate',
    'Senior Vice President', 'Vice President',
    'Project Manager', 'Group Leader', 'Team Lead',
    'Work Student', 'Werkstudent', 'Praktikant', 'Internship', 'Intern',
    'Head of',
    'SVP', 'CEO', 'COO', 'CTO', 'CFO', 'CSO', 'CMO',
    'Director',
]

# Pre-sorted longest-first so "Senior Director" matches before "Director"
ROLE_TITLES.sort(key=len, reverse=True)

_role_patterns = [(title, re.compile(r'\b' + re.escape(title) + r'\b', re.IGNORECASE))
                  for title in ROLE_TITLES]


def _extract_roles(text: str) -> list[EntityMention]:
    """Extract organizational role/title mentions."""
    mentions = []
    seen = set()

    for title, pattern in _role_patterns:
        if pattern.search(text):
            canonical = title
            if canonical in seen:
                continue
            # "Director" alone should not match if a more specific variant was already found
            if canonical == 'Director' and any(
                d in seen for d in ('Executive Director', 'Associate Director', 'Senior Director')
            ):
                continue
            seen.add(canonical)
            mentions.append(EntityMention(
                text=title,
                canonical=canonical,
                entity_type='role',
            ))

    return mentions


def _extract_methods(text: str, dicts: EntityDictionaries) -> list[EntityMention]:
    """Extract methodology/technology mentions using curated dictionary."""
    if not dicts.method_variants:
        return []
    return _extract_from_variants(text.lower(), dicts.method_variants, 'method', min_len=2)


def extract_entities(text: str, dicts: EntityDictionaries | None = None) -> EntityResult:
    """Extract all entity types from text using dictionary/regex matching (Tier 1).

    Args:
        text: Page text (markdown body, frontmatter stripped)
        dicts: Pre-loaded dictionaries, or None to auto-load

    Returns:
        EntityResult with deduplicated, canonicalized entity mentions.
    """
    if dicts is None:
        dicts = load_dictionaries()

    return EntityResult(
        compounds=_extract_compounds(text, dicts),
        genes=_extract_genes(text, dicts),
        diseases=_extract_diseases(text, dicts),
        drugs=[],  # Drugs come from LLM (Tier 2) — no curated drug dictionary
        companies=_extract_companies(text, dicts),
        roles=_extract_roles(text),
        methods=_extract_methods(text, dicts),
    )

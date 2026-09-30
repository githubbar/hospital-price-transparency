"""
Payer name normalization.

Hospital price files report payer/plan names as free text (the CMS schema has no
payer identifier), so the same insurer shows up as "BCBS", "Blue Cross Blue Shield",
"ANTHEM BC PPO", "Elevance Health", etc. This module maps each raw (payer, plan)
pair to:

  * a canonical payer  -- rows in reference/payers/canonical_payers.csv
  * a product line     -- Commercial, Medicare Advantage, Medicaid, Marketplace, ...

The mapping lives in CSV files so it can be reviewed and extended without touching
code:

  reference/payers/canonical_payers.csv  one row per canonical payer
  reference/payers/payer_aliases.csv     regex -> canonical payer, first match wins
  reference/payers/product_lines.csv     regex -> product line, first match wins

Matching order for a (payer, plan) pair:
  1. specific alias rules against the payer name, then against the plan name
     (many files use a generic payer like "MANAGED CARE [2000]" and put the real
     insurer in the plan column);
  2. fallback alias rules (fallback=yes, e.g. "MEDICARE" or "COMMERCIAL") against
     the payer name. When the payer name is nothing but the generic term, a more
     specific (earlier) fallback rule hit in the plan name wins, so
     "MEDICARE | 100% MANAGED MEDICARE" is Medicare Advantage while
     "HARVARD PILGRIM MEDICARE | MEDICARE" is not traditional Medicare. A named payer
     whose plan is just "Commercial" stays unmatched rather than being filed as
     unspecified;
  3. otherwise the pair is unmatched and lands in UNMATCHED_PAYER.
"""
import csv
import hashlib
import os
import re
from dataclasses import dataclass

REFERENCE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reference', 'payers')

UNMATCHED_PAYER = 'Other Insurers & Plans'
DEFAULT_PRODUCT_LINE = 'All / Unspecified'


def legacy_combined_raw(raw_pairs):
    """The old 'payer|plan,payer|plan' encoding of a sidebar plan's raw pairs.

    Ambiguous when names contain ',' or '|', so it is only ever hashed (see
    plan_key) or compared against, never parsed.
    """
    return ','.join(f'{p}|{pl}' for p, pl in raw_pairs)


def plan_key(raw_pairs):
    """Stable 10-char id for a sidebar plan, used as its checkbox value and in cookies.

    Hashes the legacy combined string so ids match those issued before plans were
    stored as pair lists, keeping existing 'p:<key>' cookies valid.
    """
    return hashlib.md5(legacy_combined_raw(raw_pairs).encode('utf-8')).hexdigest()[:10]


def normalize_text(s):
    """Lowercase, drop bracketed IDs ("[2000]") and date stamps ("20120101"), collapse punctuation."""
    if not s:
        return ''
    s = s.lower()
    s = re.sub(r'\[[^\]]*\]', ' ', s)
    s = re.sub(r'\b(19|20)\d{6}\b', ' ', s)
    s = s.replace('&', ' and ')
    s = re.sub(r"[^\w\s/'+-]", ' ', s)
    s = re.sub(r'[\s/_-]+', ' ', s)
    return s.strip()


@dataclass(frozen=True)
class CanonicalPayer:
    name: str
    category: str
    parent_org: str
    naic_group_code: str
    default_product_line: str
    strip_words: tuple
    sort_order: int


@dataclass(frozen=True)
class PayerMatch:
    canonical: CanonicalPayer
    product_line: str
    matched_on: str   # 'payer', 'plan' or '' when unmatched
    rule: str         # the alias pattern that matched ('' when unmatched)


CATEGORY_ORDER = ['self_pay', 'insurer', 'government', 'network', 'tpa', 'generic', 'other']
CATEGORY_LABELS = {
    'self_pay': 'Self-Pay',
    'insurer': 'Insurers',
    'government': 'Government Programs',
    'network': 'Provider Networks',
    'tpa': 'Plan Administrators (TPAs)',
    'generic': 'Unspecified Plans',
    'other': 'Other',
}


def _read_csv(path):
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return [row for row in csv.DictReader(f) if any((v or '').strip() for v in row.values())]


class PayerNormalizer:
    def __init__(self, reference_dir=REFERENCE_DIR):
        self.payers = {}
        for i, row in enumerate(_read_csv(os.path.join(reference_dir, 'canonical_payers.csv'))):
            name = row['canonical_payer'].strip()
            category = row['category'].strip()
            if category not in CATEGORY_ORDER:
                raise ValueError(f'canonical_payers.csv: unknown category {category!r} for {name!r}')
            self.payers[name] = CanonicalPayer(
                name=name,
                category=category,
                parent_org=row.get('parent_org', '').strip(),
                naic_group_code=row.get('naic_group_code', '').strip(),
                default_product_line=row.get('default_product_line', '').strip(),
                strip_words=tuple(w.strip() for w in row.get('strip_words', '').split(';') if w.strip()),
                sort_order=i,
            )
        self.unmatched = CanonicalPayer(UNMATCHED_PAYER, 'other', '', '', '', (), len(self.payers))

        self.specific_rules, self.fallback_rules = [], []
        for row in _read_csv(os.path.join(reference_dir, 'payer_aliases.csv')):
            name = row['canonical_payer'].strip()
            if name not in self.payers:
                raise ValueError(f'payer_aliases.csv: {name!r} is not in canonical_payers.csv')
            rule = (re.compile(row['pattern'].strip()), row['pattern'].strip(), self.payers[name])
            is_fallback = row.get('fallback', '').strip().lower() in ('yes', 'y', 'true', '1')
            (self.fallback_rules if is_fallback else self.specific_rules).append(rule)

        self.product_rules = [
            (re.compile(row['pattern'].strip()), row['product_line'].strip())
            for row in _read_csv(os.path.join(reference_dir, 'product_lines.csv'))
        ]

    @staticmethod
    def _first_match(rules, text):
        """Return (rule index, canonical payer, pattern, whole_text_matched) for the first matching rule."""
        for i, (regex, pattern, payer) in enumerate(rules):
            m = regex.search(text)
            if m:
                return i, payer, pattern, m.span() == (0, len(text))
        return len(rules), None, '', False

    def product_line(self, canonical, payer_text, plan_text):
        if canonical.default_product_line:
            return canonical.default_product_line
        combined = f'{payer_text} {plan_text}'
        for regex, product_line in self.product_rules:
            if regex.search(combined):
                return product_line
        return DEFAULT_PRODUCT_LINE

    def match(self, payer_raw, plan_raw=''):
        payer_text, plan_text = normalize_text(payer_raw), normalize_text(plan_raw)
        for field, text in (('payer', payer_text), ('plan', plan_text)):
            _, canonical, pattern, _ = self._first_match(self.specific_rules, text)
            if canonical:
                return PayerMatch(canonical, self.product_line(canonical, payer_text, plan_text), field, pattern)
        payer_idx, canonical, pattern, payer_is_bare = self._first_match(self.fallback_rules, payer_text)
        if canonical:
            field = 'payer'
            plan_idx, plan_canonical, plan_pattern, _ = self._first_match(self.fallback_rules, plan_text)
            if payer_is_bare and plan_idx < payer_idx:
                canonical, pattern, field = plan_canonical, plan_pattern, 'plan'
            return PayerMatch(canonical, self.product_line(canonical, payer_text, plan_text), field, pattern)
        return PayerMatch(self.unmatched, self.product_line(self.unmatched, payer_text, plan_text), '', '')

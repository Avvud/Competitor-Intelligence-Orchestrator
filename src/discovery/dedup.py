"""
dedup.py — merge duplicate competitor mentions into single entities.

The same competitor can appear as:
  - "Acme Pvt Ltd" (Google News)
  - "ACME Private Limited" (directory listing)
  - "acme.com" (competitor page link)

This module groups all those mentions into one canonical entity.

Rules (in priority order):
  1. Exact domain match (most reliable)
  2. Normalised name match (lowercase, strip legal suffixes, collapse whitespace)
  3. High Levenshtein-ratio name match (≥ 0.85) — catches typos and case variations

All rules are deterministic — no LLM calls here.
"""

import re
import uuid


# ---------------------------------------------------------------------------
# Name normalisation helpers
# ---------------------------------------------------------------------------

# Legal suffixes to strip before comparison
_LEGAL_SUFFIXES = re.compile(
    r"\b(pvt|private|public|limited|ltd|llc|inc|corp|co|llp|lp|gmbh|ag|sa|sas"
    r"|bv|nv|pte|pty|plc|sdn|bhd|s\.r\.o|s\.a|s\.p\.a)\b\.?",
    re.IGNORECASE,
)


def _normalise_name(name: str) -> str:
    """
    Lowercase, remove legal suffixes, collapse whitespace.
    "Acme Pvt Ltd." → "acme"
    "ACME Private Limited" → "acme"
    """
    name = name.lower().strip()
    name = _LEGAL_SUFFIXES.sub("", name)
    name = re.sub(r"[^\w\s]", "", name)          # remove punctuation
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _normalise_domain(domain: str | None) -> str | None:
    """
    Strip protocol, www., and trailing path from a URL/domain.
    "https://www.acme.com/about" → "acme.com"
    """
    if not domain:
        return None
    domain = re.sub(r"https?://", "", domain).split("/")[0].lower()
    domain = re.sub(r"^www\.", "", domain)
    return domain.strip() or None


def _levenshtein_ratio(a: str, b: str) -> float:
    """
    Simple Levenshtein similarity ratio in [0, 1].
    Not optimised for large strings — only used on short company names.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    # Build the DP table
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    distance = prev[lb]
    return 1.0 - distance / max(la, lb)


# ---------------------------------------------------------------------------
# Candidate data class
# ---------------------------------------------------------------------------

class CandidateEntry:
    """A single mention of a potential competitor from one source."""

    def __init__(
        self,
        name: str,
        domain: str | None = None,
        source: str = "unknown",
        source_url: str | None = None,
        website: str | None = None,
    ):
        self.name       = name.strip()
        self.domain     = _normalise_domain(domain or website)
        self.source     = source
        self.source_url = source_url
        self.website    = website


# ---------------------------------------------------------------------------
# Dedup engine
# ---------------------------------------------------------------------------

class CompetitorGroup:
    """All mentions that refer to the same company."""

    def __init__(self, seed: CandidateEntry):
        self.canonical_name = seed.name
        self.domain         = seed.domain
        self.aliases: list[str] = []
        self.sources: list[str] = [seed.source]
        self.source_urls: list[str] = [seed.source_url] if seed.source_url else []
        self.website: str | None = seed.website
        self._norm_name = _normalise_name(seed.name)

    def matches(self, entry: CandidateEntry, threshold: float = 0.85) -> bool:
        """Return True if this entry is the same company as this group."""
        # 1. Domain match (strongest signal)
        entry_domain = _normalise_domain(entry.domain)
        if entry_domain and self.domain and entry_domain == self.domain:
            return True
        # 2. Normalised name exact match
        norm = _normalise_name(entry.name)
        if norm and self._norm_name and norm == self._norm_name:
            return True
        # 3. Fuzzy name match
        if norm and self._norm_name and _levenshtein_ratio(norm, self._norm_name) >= threshold:
            return True
        return False

    def merge(self, entry: CandidateEntry):
        """Absorb another mention into this group."""
        if entry.name not in self.aliases and entry.name != self.canonical_name:
            self.aliases.append(entry.name)
        if entry.source not in self.sources:
            self.sources.append(entry.source)
        if entry.source_url and entry.source_url not in self.source_urls:
            self.source_urls.append(entry.source_url)
        # Prefer an actual website URL
        if not self.website and entry.website:
            self.website = entry.website
        if not self.domain and _normalise_domain(entry.domain):
            self.domain = _normalise_domain(entry.domain)


def dedup_candidates(
    entries: list[CandidateEntry],
    own_domain: str | None = None,
) -> list[CompetitorGroup]:
    """
    Merge entries into groups. Exclude entries whose domain matches own_domain.
    Returns list of CompetitorGroup objects (one per unique company).
    """
    groups: list[CompetitorGroup] = []

    for entry in entries:
        # Exclude our own domain
        if own_domain and entry.domain == _normalise_domain(own_domain):
            continue

        merged = False
        for group in groups:
            if group.matches(entry):
                group.merge(entry)
                merged = True
                break
        if not merged:
            groups.append(CompetitorGroup(entry))

    return groups

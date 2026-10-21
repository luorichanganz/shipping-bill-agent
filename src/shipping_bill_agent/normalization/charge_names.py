from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from shipping_bill_agent.normalization.values import normalize_text

DEFAULT_ALIASES: dict[str, str] = {
    "handling fee": "Handling Charge",
    "h/d fee": "Handling Charge",
    "h/c": "Handling Charge",
    "h/c fee": "Handling Charge",
    "handling charge": "Handling Charge",
    "doc fee": "Documentation Fee",
    "documentation fee": "Documentation Fee",
    "document fee": "Documentation Fee",
    "thc": "Terminal Handling Charge",
    "terminal handling charge": "Terminal Handling Charge",
    "trucking fee": "Trucking Charge",
    "trucking charge": "Trucking Charge",
    "customs fee": "Customs Clearance Fee",
    "customs clearance fee": "Customs Clearance Fee",
    "storage fee": "Storage Charge",
    "storage charge": "Storage Charge",
    "4 add'l detention free time charge": "Detention Charge",
    "additional detention free time charge": "Detention Charge",
    "detention charge": "Detention Charge",
    "on carriage additional - us em": "On Carriage Charge",
    "on carriage additional": "On Carriage Charge",
    "wharfage administration fee": "Wharfage Administration Fee",
    "wharfage": "Wharfage",
    "ocean freight": "Ocean Freight",
    "freight": "Ocean Freight",
}


class ChargeNameNormalizer:
    def __init__(self, aliases: dict[str, str] | None = None, fuzzy_threshold: float = 0.88):
        self.aliases = {normalize_text(key): value for key, value in (aliases or DEFAULT_ALIASES).items()}
        self.fuzzy_threshold = fuzzy_threshold

    @classmethod
    def from_file(cls, path: str | Path, fuzzy_threshold: float = 0.88) -> ChargeNameNormalizer:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Charge alias file must contain a JSON object")
        return cls({str(key): str(value) for key, value in data.items()}, fuzzy_threshold)

    def normalize(self, raw_name: str) -> tuple[str, float, str]:
        normalized = normalize_text(raw_name)
        if normalized in self.aliases:
            return self.aliases[normalized], 1.0, "exact_alias"

        collapsed = re.sub(r"\s+", " ", normalized)
        for alias, standard in self.aliases.items():
            if collapsed == re.sub(r"\s+", " ", alias):
                return standard, 0.99, "normalized_alias"

        best_name = raw_name.strip()
        best_score = 0.0
        for alias, standard in self.aliases.items():
            score = SequenceMatcher(None, collapsed, alias).ratio()
            if score > best_score:
                best_name, best_score = standard, score
        if best_score >= self.fuzzy_threshold:
            return best_name, best_score, "fuzzy_alias"
        return raw_name.strip(), best_score, "unmatched"

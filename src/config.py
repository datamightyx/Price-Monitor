"""Load and validate config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

import yaml

# Marketplace -> Amazon domain
DOMAINS = {
    "US": "www.amazon.com",
    "UK": "www.amazon.co.uk",
    "DE": "www.amazon.de",
    "FR": "www.amazon.fr",
    "IT": "www.amazon.it",
    "ES": "www.amazon.es",
    "CA": "www.amazon.ca",
    "JP": "www.amazon.co.jp",
}


@dataclass
class Group:
    name: str
    my_asin: str
    asins: List[str]

    @property
    def all_asins(self) -> List[str]:
        """my_asin first, then competitors; de-duplicated, order preserved."""
        seen, out = set(), []
        for a in [self.my_asin, *self.asins]:
            a = (a or "").strip()
            if a and a not in seen:
                seen.add(a)
                out.append(a)
        return out


@dataclass
class ScraperCfg:
    min_delay_sec: float = 4
    max_delay_sec: float = 9
    retries: int = 3
    headless: bool = True
    timeout_ms: int = 30000
    zip_code: str = ""   # US zip for non-US IPs, e.g. "10001"


@dataclass
class SheetCfg:
    enabled: bool = False
    spreadsheet: str = ""
    worksheet: str = "history"


@dataclass
class SlackCfg:
    channel: str = "#amazon-monitor"
    app_name: str = "Amazon Monitor"


@dataclass
class Config:
    marketplace: str = "US"
    language: str = "en_US"
    groups: List[Group] = field(default_factory=list)
    scraper: ScraperCfg = field(default_factory=ScraperCfg)
    google_sheet: SheetCfg = field(default_factory=SheetCfg)
    slack: SlackCfg = field(default_factory=SlackCfg)

    @property
    def domain(self) -> str:
        d = DOMAINS.get(self.marketplace.upper())
        if not d:
            raise ValueError(
                f"Unknown marketplace '{self.marketplace}'. "
                f"Known: {', '.join(DOMAINS)}"
            )
        return d


def load_config(path: str = "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    groups = []
    for g in raw.get("groups", []):
        if not g.get("name") or not g.get("my_asin"):
            raise ValueError(f"Group missing 'name' or 'my_asin': {g}")
        groups.append(
            Group(
                name=g["name"],
                my_asin=g["my_asin"],
                asins=list(g.get("asins", [])),
            )
        )
    if not groups:
        raise ValueError("config.yaml defines no groups.")

    cfg = Config(
        marketplace=raw.get("marketplace", "US"),
        language=raw.get("language", "en_US"),
        groups=groups,
        scraper=ScraperCfg(**(raw.get("scraper") or {})),
        google_sheet=SheetCfg(**(raw.get("google_sheet") or {})),
        slack=SlackCfg(**(raw.get("slack") or {})),
    )
    cfg.domain  # validate marketplace early
    return cfg

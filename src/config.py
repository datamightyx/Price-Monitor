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
    my_asins: List[str]   # one or more "my" ASINs; first is the primary
    asins: List[str]

    @property
    def my_asin(self) -> str:
        """Primary my ASIN (first in list) — kept for backward compat."""
        return self.my_asins[0] if self.my_asins else ""

    @property
    def all_asins(self) -> List[str]:
        """my_asins first, then competitors; de-duplicated, order preserved."""
        seen, out = set(), []
        for a in [*self.my_asins, *self.asins]:
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
    channel: str = "#amazon-monitor"   # channel name for chat.postMessage
    channel_id: str = ""               # channel ID (C...) for file uploads — find it in Slack: right-click channel → View channel details
    app_name: str = "Amazon Monitor"


@dataclass
class DriveCfg:
    folder_id: str = ""   # ID of the Drive folder to upload PDFs into (from URL); service account must have Editor access


@dataclass
class R2Cfg:
    bucket: str = ""        # R2 bucket name
    public_url: str = ""    # public bucket URL, e.g. https://pub-xxx.r2.dev


@dataclass
class Config:
    marketplace: str = "US"
    language: str = "en_US"
    groups: List[Group] = field(default_factory=list)
    scraper: ScraperCfg = field(default_factory=ScraperCfg)
    google_sheet: SheetCfg = field(default_factory=SheetCfg)
    slack: SlackCfg = field(default_factory=SlackCfg)
    google_drive: DriveCfg = field(default_factory=DriveCfg)
    cloudflare_r2: R2Cfg = field(default_factory=R2Cfg)

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
        # Support both my_asins (list) and legacy my_asin (single string)
        if g.get("my_asins"):
            my_asins = [a.strip() for a in g["my_asins"] if a]
        elif g.get("my_asin"):
            my_asins = [g["my_asin"].strip()]
        else:
            raise ValueError(f"Group missing 'my_asins' (or 'my_asin'): {g}")
        if not g.get("name"):
            raise ValueError(f"Group missing 'name': {g}")
        groups.append(
            Group(
                name=g["name"],
                my_asins=my_asins,
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
        google_drive=DriveCfg(**(raw.get("google_drive") or {})),
        cloudflare_r2=R2Cfg(**(raw.get("cloudflare_r2") or {})),
    )
    cfg.domain  # validate marketplace early
    return cfg

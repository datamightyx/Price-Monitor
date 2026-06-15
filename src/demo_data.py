"""Mock data so the pipeline can be exercised without scraping Amazon.

Mirrors the screenshot: today's snapshot + a yesterday baseline that produces
the same changes (IQBAR price drop, Quest deal+BSR, ALOHA BSR worsened).
"""
from __future__ import annotations

from .config import Config
from .models import Snapshot


def demo_snapshots(cfg: Config) -> dict[str, list[Snapshot]]:
    group = cfg.groups[0]
    snaps = [
        Snapshot(asin="B0B17L29N8", group=group.name, product="think! Protein Bars",
                 price=19.99, bsr=353, bsr_category="Grocery", rating=4.1, reviews=1956),
        Snapshot(asin="B07Y7H3FHB", group=group.name, product="IQBAR Clean Plant Protein Bars",
                 price=21.99, bsr=39, bsr_category="Grocery", rating=4.3, reviews=7948),
        Snapshot(asin="B0178ENI4K", group=group.name, product="ALOHA Organic Plant Based Protein",
                 price=22.15, bsr=52, bsr_category="Grocery", rating=4.5, reviews=8230),
        Snapshot(asin="B077QK3NX4", group=group.name, product="Quest Nutrition Chocolate Chip Cookie",
                 price=24.99, bsr=187, bsr_category="Grocery", rating=4.3, reviews=23924,
                 coupon="20% off", deal="-25%"),
    ]
    return {group.name: snaps}


def demo_baseline() -> dict[str, Snapshot]:
    return {
        "B0B17L29N8": Snapshot(asin="B0B17L29N8", product="think! Protein Bars",
                               price=19.99, bsr=353, rating=4.1, reviews=1956),
        "B07Y7H3FHB": Snapshot(asin="B07Y7H3FHB", product="IQBAR Clean Plant Protein Bars",
                               price=24.99, bsr=39, rating=4.3, reviews=7948),
        "B0178ENI4K": Snapshot(asin="B0178ENI4K", product="ALOHA Organic Plant Based Protein",
                               price=22.15, bsr=38, rating=4.5, reviews=8230),
        "B077QK3NX4": Snapshot(asin="B077QK3NX4", product="Quest Nutrition Chocolate Chip Cookie",
                               price=24.99, bsr=344, rating=4.3, reviews=23924),
    }

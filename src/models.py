"""Data model for a single product snapshot."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# Order of columns written to Google Sheets / used in reports.
FIELDNAMES = [
    "date",
    "group",
    "asin",
    "product",
    "brand",
    "price",
    "currency",
    "bsr",
    "bsr_category",
    "bsr2",
    "bsr_category2",
    "rating",
    "reviews",
    "coupon",
    "in_stock",
    "stp",
    "ltd",
    "fetched_at",
]


@dataclass
class Snapshot:
    asin: str
    group: str = ""
    product: Optional[str] = None
    brand: Optional[str] = None
    price: Optional[float] = None
    currency: str = "USD"
    bsr: Optional[int] = None
    bsr_category: Optional[str] = None
    bsr2: Optional[int] = None
    bsr_category2: Optional[str] = None
    rating: Optional[float] = None
    reviews: Optional[int] = None
    coupon: Optional[str] = None        # e.g. "20% off"
    stp: Optional[str] = None           # strike-through price discount, e.g. "-25%"
    ltd: Optional[str] = None           # limited time deal discount, e.g. "-27%"
    in_stock: bool = True
    error: Optional[str] = None         # set when scraping failed
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def date(self) -> str:
        """Calendar date (UTC) of the snapshot — the natural key for daily history."""
        return self.fetched_at[:10] if self.fetched_at else ""

    def to_row(self) -> dict:
        """Flat dict matching FIELDNAMES for Sheets storage."""
        return {
            "date": self.date,
            "group": self.group,
            "asin": self.asin,
            "product": self.product or "",
            "brand": self.brand or "",
            "price": "" if self.price is None else self.price,
            "currency": self.currency,
            "bsr": "" if self.bsr is None else self.bsr,
            "bsr_category": self.bsr_category or "",
            "bsr2": "" if self.bsr2 is None else self.bsr2,
            "bsr_category2": self.bsr_category2 or "",
            "rating": "" if self.rating is None else self.rating,
            "reviews": "" if self.reviews is None else self.reviews,
            "coupon": self.coupon or "",
            "stp": self.stp or "",
            "ltd": self.ltd or "",
            "in_stock": "yes" if self.in_stock else "no",
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Snapshot":
        """Rebuild a Snapshot from a Sheets row (used as diff baseline)."""
        def num(v, cast):
            if v in (None, "", "—"):
                return None
            try:
                return cast(v)
            except (ValueError, TypeError):
                return None

        return cls(
            asin=str(row.get("asin", "")).strip(),
            group=str(row.get("group", "")).strip(),
            product=(row.get("product") or None),
            brand=(row.get("brand") or None),
            price=num(row.get("price"), float),
            currency=str(row.get("currency") or "USD"),
            bsr=num(row.get("bsr"), int),
            bsr_category=(row.get("bsr_category") or None),
            bsr2=num(row.get("bsr2"), int),
            bsr_category2=(row.get("bsr_category2") or None),
            rating=num(row.get("rating"), float),
            reviews=num(row.get("reviews"), int),
            coupon=(row.get("coupon") or None),
            stp=(row.get("stp") or None),
            ltd=(row.get("ltd") or None),
            in_stock=str(row.get("in_stock", "yes")).strip().lower() != "no",
            fetched_at=str(row.get("fetched_at") or ""),
        )

    def fmt_price(self) -> str:
        """Format price with currency symbol, e.g. '$9.99'. Returns '—' if no price."""
        if self.price is None:
            return "—"
        sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(self.currency, "")
        return f"{sym}{self.price:g}"

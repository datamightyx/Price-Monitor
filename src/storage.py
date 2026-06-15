"""
Google Sheets storage.

- Appends one row per ASIN per run to a history worksheet.
- Also serves as the diff baseline: the latest row per ASIN *before today* is
  "yesterday", so the pipeline stays stateless between CI runs.

Auth: set GOOGLE_SERVICE_ACCOUNT_JSON to either the JSON content of a service
account key, or a path to the key file. Share the spreadsheet with the service
account's client_email (Editor).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .config import SheetCfg
from .models import FIELDNAMES, Snapshot

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetStore:
    def __init__(self, cfg: SheetCfg):
        self.cfg = cfg
        self._ws = None

    # -- auth / worksheet -----------------------------------------------------

    def _client(self):
        import gspread
        from google.oauth2.service_account import Credentials

        raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not raw:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
        if os.path.exists(raw):
            with open(raw, "r", encoding="utf-8") as f:
                info = json.load(f)
        else:
            info = json.loads(raw)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)

    def _worksheet(self):
        import gspread
        if self._ws is not None:
            return self._ws
        gc = self._client()
        ref = self.cfg.spreadsheet
        sh = gc.open_by_url(ref) if ref.startswith("http") else gc.open_by_key(ref)
        try:
            ws = sh.worksheet(self.cfg.worksheet)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(self.cfg.worksheet, rows=1000, cols=len(FIELDNAMES))
            ws.append_row(FIELDNAMES, value_input_option="RAW")
        # ensure header exists
        if not ws.row_values(1):
            ws.append_row(FIELDNAMES, value_input_option="RAW")
        self._ws = ws
        return ws

    # -- read baseline --------------------------------------------------------

    def latest_before(self, today: str) -> dict[str, Snapshot]:
        """Latest snapshot per ASIN with date < today. Empty dict on any failure."""
        try:
            rows = self._worksheet().get_all_records()
        except Exception as e:  # noqa: BLE001
            print(f"[sheets] baseline read failed: {e}")
            return {}
        baseline: dict[str, Snapshot] = {}
        for row in rows:
            d = str(row.get("date", ""))
            asin = str(row.get("asin", "")).strip()
            if not asin or d >= today:
                continue
            prev = baseline.get(asin)
            if prev is None or d >= prev.date:
                baseline[asin] = Snapshot.from_row(row)
        return baseline

    # -- write ----------------------------------------------------------------

    def append(self, snapshots: list[Snapshot]) -> None:
        ws = self._worksheet()
        rows = [[s.to_row()[k] for k in FIELDNAMES] for s in snapshots]
        if rows:
            ws.append_rows(rows, value_input_option="RAW")
            print(f"[sheets] appended {len(rows)} rows")


def make_store(cfg: SheetCfg) -> Optional[SheetStore]:
    if not cfg.enabled or not cfg.spreadsheet or "PUT_YOUR" in cfg.spreadsheet:
        print("[sheets] disabled or not configured — skipping storage")
        return None
    return SheetStore(cfg)

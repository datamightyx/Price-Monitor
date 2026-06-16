"""
Generate a professional PDF report covering all groups, upload it to Google Drive,
and post the shareable link to Slack.

PDF engine: Playwright (Chromium print-to-PDF) — already a project dependency,
            zero additional system libraries required on Windows or Linux.
Template:   Jinja2, embedded in this file — no external template files needed.
Drive:      Uses the same GOOGLE_SERVICE_ACCOUNT_JSON as the Sheets integration.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from jinja2 import Environment, BaseLoader

# Project root = parent of this file's directory (src/../)
_PROJECT_ROOT = Path(__file__).parent.parent

from .config import Config
from .diff import Change
from .models import Snapshot


# ── Jinja2 filters ────────────────────────────────────────────────────────────

def _f_price(s: Snapshot) -> str:
    if s.price is None:
        return "—"
    sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(s.currency, "")
    return f"{sym}{s.price:g}"


def _f_commas(n) -> str:
    if n is None:
        return "—"
    try:
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return str(n)


def _f_trunc(s: str, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _f_stars(rating) -> str:
    """Visual star string: ★★★★☆ for 4.2"""
    if rating is None:
        return "—"
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return "★" * full + ("½" if half else "") + "☆" * empty


# ── Extra helpers ─────────────────────────────────────────────────────────────

# Cyrillic lookalike → Latin (covers С А О Р Е Х Т and lowercase equivalents)
_CYRILLIC_TO_LATIN = str.maketrans("СсАаОоРрЕеХхТ", "CcAaOoRrEeXxT")


def _normalize_latin(s: str) -> str:
    """Replace Cyrillic visually-identical chars with Latin equivalents."""
    return s.translate(_CYRILLIC_TO_LATIN) if s else s


def _f_promo_val(val: str) -> str:
    """Normalize promo value: '-25%' → '−25%', empty → '—'."""
    if not val or val == "—":
        return "—"
    v = val.strip()
    m = re.match(r'^-?(\d+(?:\.\d+)?)%?$', v)
    if m:
        return f"\u2212{m.group(1)}%"          # U+2212 MINUS SIGN
    return v.replace("-", "\u2212")


def _f_change_short(c) -> str:
    """Compact change line for PDF: 'Short Name (ASIN) — detail'."""
    product = c.product or c.asin
    # Keep first 2-3 words, max 24 chars
    words = product.split()
    short = words[0] if words else product
    for w in words[1:]:
        if len(short) + 1 + len(w) <= 24:
            short += " " + w
        else:
            break
    # Strip "*Product* (ASIN) — " prefix from c.text to get just the detail
    detail = re.sub(r'^\*[^*]+\*', '', c.text).strip()
    detail = re.sub(r'^\([A-Z0-9]{10}\)\s*', '', detail).strip()
    detail = detail.lstrip("—").strip()
    return f"{short} ({c.asin}) — {detail}"


# ── HTML template ─────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
/* ── Reset & Base ───────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: "Segoe UI", "Noto Sans", "Apple Color Emoji",
               "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji",
               Arial, Helvetica, sans-serif;
  font-size: 9.5pt;
  color: #1e293b;
  background: #f1f5f9;
  line-height: 1.45;
  /* Ensure Chromium prints background colours and images */
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

/* ── Utilities ──────────────────────────────────────────────── */
.text-muted  { color: #64748b; }
.text-small  { font-size: 8pt; }
.text-tiny   { font-size: 7pt; }
.mono        { font-family: "Courier New", monospace; font-size: 8pt; }
.bold        { font-weight: 700; }
.nowrap      { white-space: nowrap; }

/* ── Report header ──────────────────────────────────────────── */
.report-header {
  background: #0f172a;
  color: #fff;
  padding: 14px 20px;
  border-radius: 10px;
  margin-bottom: 14px;
  display: table;
  width: 100%;
}
.report-header-left  { display: table-cell; vertical-align: middle; }
.report-header-right { display: table-cell; vertical-align: middle; text-align: right; }

.report-title {
  font-size: 18pt;
  font-weight: 700;
  letter-spacing: -0.3px;
  color: #f8fafc;
}
.report-tagline {
  font-size: 9pt;
  color: #94a3b8;
  margin-top: 3px;
}
.report-date-big {
  font-size: 16pt;
  font-weight: 700;
  color: #f8fafc;
}
.report-date-sub {
  font-size: 8.5pt;
  color: #94a3b8;
  margin-top: 3px;
}

/* ── KPI cards ──────────────────────────────────────────────── */
.kpi-row {
  display: table;
  width: 100%;
  border-spacing: 10px 0;
  margin-bottom: 14px;
}
.kpi-cell { display: table-cell; width: 20%; }

.kpi-card {
  background: #ffffff;
  border-radius: 8px;
  padding: 12px 14px 10px;
  border-top: 3px solid #94a3b8;
  box-shadow: 0 1px 3px rgba(0,0,0,.07);
  text-align: center;
}
.kpi-card.blue   { border-top-color: #2563eb; }
.kpi-card.green  { border-top-color: #059669; }
.kpi-card.red    { border-top-color: #dc2626; }
.kpi-card.amber  { border-top-color: #d97706; }
.kpi-card.purple { border-top-color: #7c3aed; }

.kpi-value {
  font-size: 26pt;
  font-weight: 700;
  line-height: 1;
  color: #0f172a;
}
.kpi-card.blue   .kpi-value { color: #2563eb; }
.kpi-card.green  .kpi-value { color: #059669; }
.kpi-card.red    .kpi-value { color: #dc2626; }
.kpi-card.amber  .kpi-value { color: #d97706; }
.kpi-card.purple .kpi-value { color: #7c3aed; }

.kpi-label {
  font-size: 7pt;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  margin-top: 5px;
  font-weight: 600;
}

/* ── Group wrapper ──────────────────────────────────────────── */
.group-wrap {
  background: #ffffff;
  border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,.09);
  margin-bottom: 18px;
  overflow: hidden;
}

/* ── Group header ───────────────────────────────────────────── */
.group-header {
  background: #1e40af;
  color: #fff;
  padding: 10px 16px;
  display: table;
  width: 100%;
}
.gh-left  { display: table-cell; vertical-align: middle; }
.gh-right { display: table-cell; vertical-align: middle; text-align: right; }
.group-name { font-size: 13pt; font-weight: 700; }
.group-meta { font-size: 8pt; color: #bfdbfe; margin-top: 2px; }
.group-my-asin { font-size: 8pt; color: #bfdbfe; }
.group-my-asin strong { color: #fff; }

/* ── My-product highlight bar ───────────────────────────────── */
.my-product-bar {
  background: #eff6ff;
  border-bottom: 1px solid #bfdbfe;
  padding: 7px 16px;
  display: table;
  width: 100%;
  table-layout: fixed;
  box-sizing: border-box;
}
/* col widths: name=40%, price=10%, bsr=15%, rating=10%, reviews=10%, promos=15% */
.mp-col          { display: table-cell; vertical-align: middle; padding-right: 12px; overflow: hidden; }
.mp-col-name     { width: 40%; }
.mp-col-price    { width: 10%; }
.mp-col-bsr      { width: 15%; }
.mp-col-rating   { width: 10%; }
.mp-col-reviews  { width: 10%; }
.mp-col-promos   { width: 15%; }
.mp-eyebrow {
  font-size: 7pt;
  font-weight: 700;
  color: #2563eb;
  text-transform: uppercase;
  letter-spacing: 0.6px;
}
.mp-name {
  font-size: 9.5pt;
  font-weight: 700;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}
.mp-asin { font-size: 7pt; color: #64748b; font-family: monospace; }
.mp-stat { font-size: 8.5pt; color: #475569; }
.mp-stat strong { color: #0f172a; font-weight: 700; }

/* ── Product table ──────────────────────────────────────────── */
.product-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 9.5pt;
  line-height: 1.5;
}
.product-table thead tr {
  background: #f1f5f9;
  border-bottom: 2px solid #cbd5e1;
}
.product-table th {
  padding: 8px 10px;
  text-align: left;
  font-size: 7.5pt;
  font-weight: 700;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  white-space: nowrap;
}
.product-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #e2e8f0;
  vertical-align: middle;
}
.product-table tbody tr:last-child td { border-bottom: none; }
.product-table tbody tr:nth-child(even) { background: #f8fafc; }
.product-table tbody tr.is-mine { background: #dbeafe !important; }
.product-table tbody tr.has-error { opacity: 0.55; }

.star-marker {
  color: #2563eb;
  font-size: 11pt;
  font-weight: 700;
  text-align: center;
}
.product-name { width: 210px; }
.product-name-text {
  font-weight: 500;
  white-space: normal;
  word-break: break-word;
  display: block;
  overflow: hidden;
  max-height: 4.5em;
}
.is-mine .product-name-text { font-weight: 700; }
.error-note { font-size: 7pt; color: #dc2626; }

/* Category: wrap instead of truncating */
.category-cell {
  max-width: 110px;
  white-space: normal;
  word-break: break-word;
  font-size: 8.5pt;
  color: #64748b;
}

/* Table footnote */
.table-legend {
  font-size: 7pt;
  color: #94a3b8;
  padding: 5px 8px 8px;
  border-top: 1px solid #f1f5f9;
}
.table-legend strong { color: #64748b; }

/* ── Badges ─────────────────────────────────────────────────── */
.badge {
  display: inline-block;
  padding: 1.5px 5px;
  border-radius: 4px;
  font-size: 7.5pt;
  font-weight: 700;
  white-space: nowrap;
}
.badge-yes     { background: #d1fae5; color: #065f46; }
.badge-no      { background: #fee2e2; color: #991b1b; }
.badge-error   { background: #fef3c7; color: #92400e; }
.badge-coupon  { background: #ede9fe; color: #5b21b6; }
.badge-stp     { background: #fef9c3; color: #854d0e; }
.badge-ltd     { background: #fee2e2; color: #9f1239; }

/* ── Rating ─────────────────────────────────────────────────── */
.rating-stars { color: #f59e0b; letter-spacing: -1px; }
.rating-num   { color: #64748b; font-size: 8pt; margin-left: 2px; }

/* ── Bottom panel (changes + promos, stacked vertically) ────── */
.bottom-panel {
  width: 100%;
  border-top: 2px solid #f1f5f9;
}
.bp-col { padding: 12px 16px; }
.bp-col + .bp-col { border-top: 1px solid #f1f5f9; }

.panel-title {
  font-size: 8pt;
  font-weight: 700;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  padding-bottom: 7px;
  border-bottom: 1px solid #e2e8f0;
  margin-bottom: 8px;
}

/* Changes list */
.change-item {
  display: table;
  width: 100%;
  padding: 3px 0;
  font-size: 8.5pt;
}
.change-emoji { display: table-cell; width: 18px; vertical-align: top; }
.change-text  { display: table-cell; vertical-align: top; color: #334155; }
.no-content   { font-size: 8pt; color: #94a3b8; font-style: italic; }

/* Promos grid */
.promos-grid {
  display: table;
  width: 100%;
  border-spacing: 0 5px;
}
.promo-row { display: table-row; }
.promo-cell { display: table-cell; width: 33%; padding-right: 8px; }

.promo-card {
  border-radius: 5px;
  padding: 6px 8px;
  font-size: 8pt;
  border-left: 3px solid #e2e8f0;
}
.promo-card.is-coupon { background: #f5f3ff; border-left-color: #7c3aed; }
.promo-card.is-stp    { background: #fffbeb; border-left-color: #d97706; }
.promo-card.is-ltd    { background: #fff1f2; border-left-color: #e11d48; }

.promo-type-label {
  font-size: 7pt;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 1px;
}
.promo-card.is-coupon .promo-type-label { color: #7c3aed; }
.promo-card.is-stp    .promo-type-label { color: #b45309; }
.promo-card.is-ltd    .promo-type-label { color: #be123c; }

.promo-product-name {
  font-weight: 600;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}
.promo-brand {
  display: block;
  font-size: 7pt;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.promo-asin {
  display: block;
  font-size: 7pt;
  color: #94a3b8;
  font-family: monospace;
}
.promo-value { color: #0f172a; font-size: 10pt; font-weight: 700; margin-top: 3px; }

/* ── Page break between groups ──────────────────────────────── */
.group-break { page-break-before: always; }
</style>
</head>
<body>

<!-- ════════════════════════════════════════════════════════════
     REPORT HEADER
════════════════════════════════════════════════════════════ -->
<div class="report-header">
  <div class="report-header-left">
    <div class="report-title">Amazon Competitive Monitor</div>
    <div class="report-tagline">
      Weekly Performance Report &nbsp;·&nbsp;
      {{ total_groups }} group{{ 's' if total_groups != 1 else '' }}
      &nbsp;·&nbsp;
      {{ total_asins }} ASINs tracked
    </div>
  </div>
  <div class="report-header-right">
    <div class="report-date-big">{{ today_pretty }}</div>
    <div class="report-date-sub">Generated {{ generated_at }}</div>
  </div>
</div>

<!-- ════════════════════════════════════════════════════════════
     KPI SUMMARY
════════════════════════════════════════════════════════════ -->
<div class="kpi-row">
  <div class="kpi-cell">
    <div class="kpi-card blue">
      <div class="kpi-value">{{ total_asins }}</div>
      <div class="kpi-label">Products Tracked</div>
    </div>
  </div>
  <div class="kpi-cell">
    <div class="kpi-card green">
      <div class="kpi-value">{{ in_stock_count }}</div>
      <div class="kpi-label">In Stock</div>
    </div>
  </div>
  <div class="kpi-cell">
    <div class="kpi-card red">
      <div class="kpi-value">{{ out_of_stock_count }}</div>
      <div class="kpi-label">Out of Stock</div>
    </div>
  </div>
  <div class="kpi-cell">
    <div class="kpi-card amber">
      <div class="kpi-value">{{ promo_count }}</div>
      <div class="kpi-label">Active Promos</div>
    </div>
  </div>
  <div class="kpi-cell">
    <div class="kpi-card purple">
      <div class="kpi-value">{{ total_changes }}</div>
      <div class="kpi-label">Changes Detected</div>
    </div>
  </div>
</div>

<!-- ════════════════════════════════════════════════════════════
     PER-GROUP SECTIONS
════════════════════════════════════════════════════════════ -->
{% for gd in groups %}
<div class="group-wrap{% if not loop.first %} group-break{% endif %}">

  <!-- Group header -->
  <div class="group-header">
    <div class="gh-left">
      <div class="group-name">{{ gd.name }}</div>
      <div class="group-meta">
        {{ gd.snaps | length }} product{{ 's' if gd.snaps | length != 1 else '' }}
        &nbsp;·&nbsp;
        {{ gd.changes | length }} change{{ 's' if gd.changes | length != 1 else '' }} detected
      </div>
    </div>
    <div class="gh-right">
      <div class="group-my-asin">My ASIN{{ 's' if gd.my_asins | length > 1 else '' }}:
        <strong>{{ gd.my_asins | join(', ') }}</strong>
      </div>
    </div>
  </div>

  <!-- Product comparison table -->
  <table class="product-table">
    <thead>
      <tr>
        <th style="width:14px;"></th>
        <th>ASIN</th>
        <th>Product</th>
        <th>Brand</th>
        <th>Price</th>
        <th>BSR</th>
        <th>Category</th>
        <th>Rating</th>
        <th style="text-align:right;">Reviews</th>
        <th style="text-align:center;">In Stock</th>
        <th>Coupon</th>
        <th>STP</th>
        <th>LTD</th>
      </tr>
    </thead>
    <tbody>
    {% for s in gd.snaps %}
    <tr class="{% if s.asin in gd.my_asins %}is-mine{% endif %}{% if s.error %} has-error{% endif %}">
      <td class="star-marker">{% if s.asin in gd.my_asins %}★{% endif %}</td>
      <td class="mono nowrap"><a href="https://{{ domain }}/dp/{{ s.asin }}" target="_blank" style="color:#2563eb;text-decoration:none;">{{ s.asin }}</a></td>
      <td class="product-name">
        <span class="product-name-text" title="{{ s.product or '' }}">
          {{ s.product if s.product else '—' }}
        </span>
        {% if s.error %}<span class="error-note">⚠ fetch error</span>{% endif %}
      </td>
      <td>{{ s.brand | trunc(16) if s.brand else '—' }}</td>
      <td class="bold nowrap">{{ s | price }}</td>
      <td class="nowrap">
        {% if s.bsr is not none %}#{{ s.bsr | commas }}{% else %}—{% endif %}
      </td>
      <td class="category-cell">{{ s.bsr_category if s.bsr_category else '—' }}</td>
      <td class="nowrap">
        {% if s.rating is not none %}
          <span class="rating-stars">{{ s.rating | stars }}</span>
          <span class="rating-num">{{ s.rating }}</span>
        {% else %}—{% endif %}
      </td>
      <td style="text-align:right;">
        {% if s.reviews is not none %}{{ s.reviews | commas }}{% else %}—{% endif %}
      </td>
      <td style="text-align:center;">
        {% if s.error %}
          <span class="badge badge-error">err</span>
        {% elif s.in_stock %}
          <span class="badge badge-yes">✓</span>
        {% else %}
          <span class="badge badge-no">✗</span>
        {% endif %}
      </td>
      <td>
        {% if s.coupon %}<span class="badge badge-coupon">{{ s.coupon | promo_val }}</span>{% else %}—{% endif %}
      </td>
      <td>
        {% if s.stp %}<span class="badge badge-stp">{{ s.stp | promo_val }}</span>{% else %}—{% endif %}
      </td>
      <td>
        {% if s.ltd %}<span class="badge badge-ltd">{{ s.ltd | promo_val }}</span>{% else %}—{% endif %}
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>

  <!-- Bottom panel: changes + promotions -->
  <div class="bottom-panel">
    <!-- Changes column -->
    <div class="bp-col">
      <div class="panel-title">📊 Changes vs Previous Report</div>
      {% if gd.changes %}
        {% for c in gd.changes %}
        <div class="change-item">
          <div class="change-emoji">{{ c.emoji }}</div>
          <div class="change-text">{{ c | change_short }}</div>
        </div>
        {% endfor %}
      {% else %}
        <div class="no-content">No changes detected since last report.</div>
      {% endif %}
    </div>

    <!-- Promotions column -->
    <div class="bp-col">
      <div class="panel-title">🎯 Active Promotions</div>
      {% set promos = namespace(items=[]) %}
      {% for s in gd.snaps %}{% if not s.error %}
        {% if s.coupon %}{% set promos.items = promos.items + [('coupon', s, s.coupon)] %}{% endif %}
        {% if s.stp    %}{% set promos.items = promos.items + [('stp',    s, s.stp)]    %}{% endif %}
        {% if s.ltd    %}{% set promos.items = promos.items + [('ltd',    s, s.ltd)]    %}{% endif %}
      {% endif %}{% endfor %}

      {% if promos.items %}
      <div class="promos-grid">
        {% for row in promos.items | batch(3) %}
        <div class="promo-row">
          {% for ptype, s, val in row %}
          <div class="promo-cell">
            <div class="promo-card is-{{ ptype }}">
              <div class="promo-type-label">{{ ptype | upper }}</div>
              <span class="promo-product-name">{{ (s.product or s.asin) | trunc(28) }}</span>
              <span class="promo-brand">{{ s.brand or '' }}</span>
              <span class="promo-asin">{{ s.asin }}</span>
              <div class="promo-value">{{ val | promo_val }}</div>
            </div>
          </div>
          {% endfor %}
        </div>
        {% endfor %}
      </div>
      {% else %}
        <div class="no-content">No active promotions.</div>
      {% endif %}
    </div>
  </div>

</div><!-- /group-wrap -->
{% endfor %}

</body>
</html>"""


# ── PDF generation ────────────────────────────────────────────────────────────

_FOOTER_TEMPLATE = """
<div style="width:100%; font-size:8px; color:#94a3b8;
            font-family:'Segoe UI',Arial,sans-serif;
            display:flex; justify-content:space-between;
            padding:0 14mm; box-sizing:border-box;">
  <span>Amazon Competitive Monitor</span>
  <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>
"""


def _render_html(
    cfg: Config,
    results: dict[str, list[Snapshot]],
    all_changes: dict[str, list[Change]],
    today: str,
) -> str:
    """Render and return the HTML string for the report."""
    all_snaps = [s for snaps in results.values() for s in snaps]
    in_stock_count     = sum(1 for s in all_snaps if s.in_stock and not s.error)
    out_of_stock_count = sum(1 for s in all_snaps if not s.in_stock and not s.error)
    promo_count        = sum(1 for s in all_snaps if (s.coupon or s.stp or s.ltd) and not s.error)
    total_changes      = sum(len(c) for c in all_changes.values())

    groups_ctx = []
    for group in cfg.groups:
        snaps   = results.get(group.name, [])
        changes = all_changes.get(group.name, [])
        my_set   = set(group.my_asins)
        my_snaps = [s for s in snaps if s.asin in my_set]
        groups_ctx.append({
            "name":     _normalize_latin(group.name),
            "my_asins": group.my_asins,         # full list
            "my_asin":  group.my_asin,          # primary (first) — kept for compat
            "snaps":    snaps,
            "changes":  changes,
            "my_snaps": my_snaps,               # list of my-product snapshots
            "my_snap":  my_snaps[0] if my_snaps else None,  # primary for the highlight bar
        })

    try:
        dt = date.fromisoformat(today)
        today_pretty = dt.strftime("%A, %B %d, %Y")
    except Exception:
        today_pretty = today

    env = Environment(loader=BaseLoader(), autoescape=False)
    env.filters["price"]        = _f_price
    env.filters["commas"]       = _f_commas
    env.filters["trunc"]        = _f_trunc
    env.filters["stars"]        = _f_stars
    env.filters["promo_val"]    = _f_promo_val
    env.filters["change_short"] = _f_change_short

    return env.from_string(_HTML).render(
        today=today,
        today_pretty=today_pretty,
        generated_at=datetime.now().strftime("%H:%M UTC"),
        total_groups=len(cfg.groups),
        total_asins=len(all_snaps),
        in_stock_count=in_stock_count,
        out_of_stock_count=out_of_stock_count,
        promo_count=promo_count,
        total_changes=total_changes,
        groups=groups_ctx,
        domain=cfg.domain,
    )


def generate_pdf(
    cfg: Config,
    results: dict[str, list[Snapshot]],
    all_changes: dict[str, list[Change]],
    today: str,
) -> bytes:
    """Render the HTML report to PDF using Playwright/Chromium (no extra system libs)."""
    from playwright.sync_api import sync_playwright

    html_str = _render_html(cfg, results, all_changes, today)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_str, wait_until="networkidle")
        pdf_bytes = page.pdf(
            format="A4",
            landscape=True,
            print_background=True,
            margin={"top": "12mm", "right": "14mm", "bottom": "18mm", "left": "14mm"},
            display_header_footer=True,
            header_template="<span></span>",   # empty header
            footer_template=_FOOTER_TEMPLATE,
        )
        browser.close()

    return pdf_bytes


# ── Cloudflare R2 upload ──────────────────────────────────────────────────────


def _upload_to_r2(pdf_bytes: bytes, filename: str, cfg_r2) -> Optional[str]:
    """Upload PDF to Cloudflare R2 (S3-compatible).

    Required env vars: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
    Optional config:   cloudflare_r2.public_url  → returned as the shareable link
    """
    account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
    access_key = os.getenv("R2_ACCESS_KEY_ID", "").strip()
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()

    if not all([account_id, access_key, secret_key]):
        print("[report] R2 credentials not set (R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY) — skipping R2 upload")
        return None

    if not cfg_r2.bucket:
        print("[report] cloudflare_r2.bucket not set in config — skipping R2 upload")
        return None

    import boto3
    from botocore.config import Config as BotoConfig

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )

    s3.put_object(
        Bucket=cfg_r2.bucket,
        Key=filename,
        Body=pdf_bytes,
        ContentType="application/pdf",
    )

    if cfg_r2.public_url:
        url = f"{cfg_r2.public_url.rstrip('/')}/{filename}"
    else:
        url = None  # no public URL configured — file uploaded but not linkable

    print(f"[report] PDF uploaded to R2 bucket '{cfg_r2.bucket}': {url or '(no public URL)'}")

    # Keep at most 30 files — delete oldest by LastModified
    _prune_r2_bucket(s3, cfg_r2.bucket, keep=30)

    return url


def _prune_r2_bucket(s3, bucket: str, keep: int = 30) -> None:
    """Delete oldest files from the bucket if total count exceeds *keep*."""
    response = s3.list_objects_v2(Bucket=bucket)
    objects = response.get("Contents", [])
    if len(objects) <= keep:
        return
    objects.sort(key=lambda o: o["LastModified"])
    to_delete = objects[:len(objects) - keep]
    s3.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": o["Key"]} for o in to_delete]},
    )
    print(f"[report] R2 pruned {len(to_delete)} old file(s), keeping {keep}")


# ── Slack notification ────────────────────────────────────────────────────────


def _post_link_to_slack(cfg: Config, drive_url: str, today: str) -> None:
    """Post a Slack message with the PDF link.
    Works with both SLACK_BOT_TOKEN (chat.postMessage) and SLACK_WEBHOOK_URL."""
    token   = os.getenv("SLACK_BOT_TOKEN")
    webhook = os.getenv("SLACK_WEBHOOK_URL")

    if not token and not webhook:
        print("[report] no Slack credentials — skipping notification")
        return

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📊 Price Monitor Report — {today}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":page_facing_up: <{drive_url}|*Open PDF report*>",
            },
        },
    ]
    fallback = f"📊 Price Monitor Report — {today}: {drive_url}"

    if token:
        channel = cfg.slack.channel_id or cfg.slack.channel
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"channel": channel, "blocks": blocks, "text": fallback},
            timeout=20,
        )
        d = resp.json()
        if not d.get("ok"):
            print(f"[report] chat.postMessage error: {d.get('error')}")
        else:
            print(f"[report] Drive link posted to {cfg.slack.channel} ✓")
    else:
        resp = requests.post(webhook,
                             json={"blocks": blocks, "text": fallback},
                             timeout=20)
        resp.raise_for_status()
        print("[report] Drive link posted via webhook ✓")


# ── Public entry point ────────────────────────────────────────────────────────


def send_pdf(cfg: Config, pdf_bytes: bytes, today: str) -> str:
    """Save the PDF to disk, upload to Google Drive, and post the link to Slack.

    Always writes the file so GitHub Actions can pick it up as an artifact.
    Returns the local filename that was written.
    """
    filename = f"amazon_report_{today}.pdf"
    filepath = _PROJECT_ROOT / filename

    filepath.write_bytes(pdf_bytes)
    print(f"[report] PDF saved → {filepath}")

    drive_url = _upload_to_r2(pdf_bytes, filename, cfg.cloudflare_r2)
    if drive_url:
        _post_link_to_slack(cfg, drive_url, today)

    return filename

#!/usr/bin/env python3
"""
Daily Scraper - extracts today's lease deals from leasehackr.com and pushes to a 'Daily' Google Sheets tab.
- Wipes the sheet fresh each run (keeps headers intact)
- Writes only today's scraped deals, sorted by score
- Deduplicates within today's scrape
- Telegram alert only if any deal scores ≥ 98
"""

import os
import json
from dataclasses import dataclass, asdict
from typing import Optional

import requests
from scrapling import StealthyFetcher
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
import gspread
from urllib.parse import urlparse, parse_qs

# ── Shared helpers (imported from scraper.py) ────────────────────────────────
import scraper

# ── Constants ────────────────────────────────────────────────────────────────
DAILY_SHEET_NAME = os.environ.get("SHEET2_NAME", "Daily")   # Tab name in Google Sheets
TELEGRAM_ALERT_THRESHOLD = 98.0   # Only alert if deal score >= 98

# ── Column headers (must match what's already in your Daily sheet tab) ────────
HEADERS = [
    'Make', 'Model', 'MSRP', 'Sales Price', 'Months', 'Miles/Year',
    'Monthly Payment', 'Due at Signing', 'Sales Tax', 'Money Factor',
    'Interest Rate %', 'Residual %', 'Score'
]


def get_daily_worksheet(client: gspread.Client, spreadsheet_id: str):
    """
    Open or create the 'Daily' worksheet within the spreadsheet.
    """
    spreadsheet = client.open_by_key(spreadsheet_id)

    # Try to find an existing sheet named "Daily"
    try:
        worksheet = spreadsheet.worksheet(DAILY_SHEET_NAME)
        print(f"Found existing worksheet: '{DAILY_SHEET_NAME}'")
    except gspread.exceptions.WorksheetNotFound:
        # Add a new sheet
        worksheet = spreadsheet.add_worksheet(
            title=DAILY_SHEET_NAME,
            rows=1,
            cols=13
        )
        print(f"Created new worksheet: '{DAILY_SHEET_NAME}'")

    return worksheet


def clear_sheet_keep_headers(worksheet) -> None:
    """
    Clear all data rows below the header row (row 1) without deleting the header row.
    gspread's clear() wipes everything, so we fetch, keep headers, clear, then re-write headers.
    """
    all_values = worksheet.get_all_values()
    worksheet.clear()
    worksheet.append_row(HEADERS)
    if all_values and len(all_values) > 1:
        print(f"Cleared {len(all_values) - 1} previous data rows. Headers written: {HEADERS}")
    else:
        print(f"Sheet ready. Headers written: {HEADERS}")


def filter_hot_deals(deals: list, threshold: float = TELEGRAM_ALERT_THRESHOLD) -> list:
    """
    Return only deals with a score >= threshold.
    """
    return [deal for deal in deals if deal.score >= threshold]


def send_daily_telegram_alert(hot_deals: list) -> None:
    """
    Send a Telegram alert listing all deals scoring >= 98.
    """
    if not hot_deals:
        return

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials not found. Skipping alert.")
        return

    text = f"🚨 Leasehackr Daily Alert: {len(hot_deals)} Deal(s) Score ≥ {TELEGRAM_ALERT_THRESHOLD}!\n\n"
    for deal in hot_deals:
        text += (
            f"🔥 Score: {deal.score}/100\n"
            f"🚗 {deal.make} {deal.model}\n"
            f"💰 ${deal.monthly_payment}/mo (${deal.due_at_signing} DAS)\n"
            f"🏷️ MSRP: {deal.msrp} | Term: {deal.months} mo\n"
            f"📊 Interest: {deal.interest_rate}% | Residual: {deal.residual_percent}%\n\n"
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        resp = requests.post(url, json=payload)
        if resp.status_code == 200:
            print(f"Telegram alert sent successfully ({len(hot_deals)} deal(s))!")
        else:
            print(f"Telegram alert failed: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")


def deduplicate_deals(deals: list) -> list:
    """
    Deduplicate deals by signature (keep first occurrence).
    """
    seen = set()
    unique = []
    for deal in deals:
        sig = deal.signature
        if sig not in seen:
            seen.add(sig)
            unique.append(deal)
    return unique


def sort_deals_by_score(deals: list) -> list:
    """
    Sort deals descending by score.
    """
    return sorted(deals, key=lambda d: d.score, reverse=True)


def main():
    """Main function to run the daily scraper."""
    print("=" * 60)
    print("DAILY SCRAPER — Leasehackr")
    print("=" * 60)

    # ── 1. Connect to Google Sheets ──────────────────────────────────────
    print("\n[1/5] Loading credentials and connecting to Google Sheets...")
    client = scraper.get_google_client()
    spreadsheet_id = scraper.get_spreadsheet_id()
    worksheet = get_daily_worksheet(client, spreadsheet_id)

    # ── 2. Clear data rows, keep headers ──────────────────────────────────
    print("\n[2/5] Clearing previous daily data (keeping headers)...")
    clear_sheet_keep_headers(worksheet)

    # ── 3. Scrape today's deals ───────────────────────────────────────────
    print("\n[3/5] Scraping today's deals from leasehackr.com...")
    scraped_deals = scraper.scrape_deals()
    print(f"  → Scraped {len(scraped_deals)} deals total")

    # ── 4. Deduplicate & sort ─────────────────────────────────────────────
    print("\n[4/5] Deduplicating and sorting by score...")
    unique_deals = deduplicate_deals(scraped_deals)
    sorted_deals = sort_deals_by_score(unique_deals)
    print(f"  → {len(unique_deals)} unique deals after dedup")

    # Preview top 5
    print("\n  === Today's Top 5 Deals ===")
    for i, deal in enumerate(sorted_deals[:5], 1):
        print(f"  {i}. Score: {deal.score}/100 — {deal.make} {deal.model} — ${deal.monthly_payment}/mo")

    # ── 5. Write to Daily sheet ───────────────────────────────────────────
    print(f"\n[5/5] Writing {len(sorted_deals)} deals to the '{DAILY_SHEET_NAME}' sheet...")
    if sorted_deals:
        rows = [deal.to_list() for deal in sorted_deals]
        worksheet.append_rows(rows)

    print(f"\n✅ Daily sheet refreshed with {len(sorted_deals)} deals!")

    # ── 6. Telegram alert (score >= 98 only) ──────────────────────────────
    hot_deals = filter_hot_deals(sorted_deals, threshold=TELEGRAM_ALERT_THRESHOLD)
    print(f"\n[Alert Check] {len(hot_deals)} deal(s) with score ≥ {TELEGRAM_ALERT_THRESHOLD}")
    if hot_deals:
        send_daily_telegram_alert(hot_deals)
    else:
        print("  No deals met the alert threshold — no Telegram message sent.")

    print("\n" + "=" * 60)
    print("DAILY SCRAPER — DONE")
    print("=" * 60)


if __name__ == "__main__":
    main()

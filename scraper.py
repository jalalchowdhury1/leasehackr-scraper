#!/usr/bin/env python3
"""
Scraper script to extract lease deals from leasehackr.com and push to Google Sheets.
Fetches every regional board via fetcher.fetch_all_regions() (each region tried
requests → Lightpanda → scrapling) and uses gspread to write to Google Sheets.
"""

import os
import json
import time
from dataclasses import dataclass, asdict
from typing import Optional

import requests
from bs4 import BeautifulSoup

from fetcher import REGIONS, fetch_all_regions
from google.oauth2.service_account import Credentials
import gspread
from urllib.parse import urlparse, parse_qs


# Telegram alert fires only for brand-new deals (first time seen) scoring at or
# above this value. Mirrors scraper_daily.py so both scrapers use the same bar.
TELEGRAM_ALERT_THRESHOLD = 98.0


@dataclass
class LeaseDeal:
    """Dataclass representing a lease deal with named properties."""
    make: str = ''
    model: str = ''
    msrp: str = ''
    sales_price: str = ''
    months: str = ''
    miles_per_year: str = ''
    monthly_payment: str = ''
    due_at_signing: str = ''
    sales_tax: str = ''
    money_factor: str = ''
    interest_rate: str = ''
    residual_percent: str = ''
    score: float = 0.0
    # One-pay lease: paid once upfront, so the card's monthly is 0 and
    # due_at_signing is the single payment. Deliberately NOT a sheet column and
    # NOT in the signature — the 13-column layout and dedup stay untouched.
    one_pay: bool = False

    def to_list(self) -> list:
        """Convert deal to list format for Google Sheets (matching header order)."""
        return [
            self.make,
            self.model,
            self.msrp,
            self.sales_price,
            self.months,
            self.miles_per_year,
            self.monthly_payment,
            self.due_at_signing,
            self.sales_tax,
            self.money_factor,
            self.interest_rate,
            self.residual_percent,
            self.score
        ]

    def to_dict(self) -> dict:
        """Convert deal to dictionary format."""
        return asdict(self)

    @property
    def signature(self) -> tuple:
        """Return a tuple that uniquely identifies this deal for deduplication."""
        return (
            self.make.strip(),
            self.model.strip(),
            self.msrp.strip(),
            self.monthly_payment.strip()
        )


def calculate_score(msrp: str, monthly: str, das: str, months: str) -> float:
    """
    Calculate a 0-100 score based on the 1% rule (0.8% = 100 score, 1.8% = 0 score).
    """
    try:
        m_val = float(str(msrp).replace('$', '').replace(',', ''))
        mo_val = float(str(monthly).replace('$', '').replace(',', ''))
        das_val = float(str(das).replace('$', '').replace(',', ''))
        mos_val = float(months)

        effective_monthly = mo_val + (das_val / mos_val)
        ratio = effective_monthly / m_val

        score = 100 - ((ratio - 0.008) / 0.010) * 100
        return max(0, min(100, round(score, 1)))  # Clamp between 0 and 100
    except (ValueError, ZeroDivisionError, TypeError):
        return 0


def _fmt_money(value) -> str:
    """Format a money value as '$1,234'. Accepts bare numbers or $-prefixed strings."""
    s = str(value).strip().lstrip("$").replace(",", "").strip()
    if not s:
        return "N/A"
    try:
        return f"${int(round(float(s))):,}"
    except (ValueError, TypeError):
        return f"${s}"


def payment_line(deal) -> str:
    """The 💰 alert line. A one-pay lease has no monthly bill, so show the upfront
    payment and what it works out to per month instead of a misleading '$0/mo'.
    Shared by both scrapers' alerts."""
    if deal.one_pay:
        line = f"💰 One-pay: {_fmt_money(deal.due_at_signing)} upfront"
        try:
            das = float(str(deal.due_at_signing).replace('$', '').replace(',', ''))
            return f"{line} (≈{_fmt_money(das / float(deal.months))}/mo)"
        except (ValueError, ZeroDivisionError, TypeError):
            return line
    return f"💰 {_fmt_money(deal.monthly_payment)}/mo ({_fmt_money(deal.due_at_signing)} DAS)"


def send_telegram_alert(hot_deals: list) -> None:
    """
    Send a Telegram alert for brand-new deals scoring >= TELEGRAM_ALERT_THRESHOLD.
    """
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials not found. Skipping alert.")
        return

    text = f"🆕 Leasehackr: {len(hot_deals)} New Deal(s) Scoring ≥ {TELEGRAM_ALERT_THRESHOLD}! 🆕\n\n"
    for deal in hot_deals:
        text += (
            f"🔥 Score: {deal.score}/100\n"
            f"🚗 {deal.make} {deal.model}\n"
            f"{payment_line(deal)}\n"
            f"🏷️ MSRP: {_fmt_money(deal.msrp)} | Term: {deal.months} mo\n"
            f"📊 Interest: {deal.interest_rate}% | Residual: {deal.residual_percent}%\n\n"
        )
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload)
        print("Telegram alert sent successfully!")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")


# ── Google Sheets retry ─────────────────────────────────────────────────────
# Every Sheets call goes through sheets_call(). Sheets returns a transient 503
# often enough to matter: on 2026-08-27 one came back from the *metadata read*
# inside `spreadsheet.worksheet("Daily")` and killed the whole Daily run before
# a single deal was scraped, while the Historical run 30 s later was fine.
#
# Retry only what a retry can fix. 429 and 5xx mean the request was REJECTED —
# nothing was applied, so re-sending an append cannot duplicate rows. 403/404
# (bad credentials, wrong spreadsheet id) fail the same way forever, and
# WorksheetNotFound is not an error at all here — scraper_daily catches it to
# create the Daily tab on first run, so it must reach the caller untouched.
SHEETS_RETRY_ATTEMPTS = 4
SHEETS_RETRY_BASE_S = 2
RETRYABLE_SHEETS_STATUS = frozenset({429, 500, 502, 503, 504})


def _sheets_status(exc) -> Optional[int]:
    """HTTP status behind a gspread APIError.

    gspread 5.12 (pinned here) exposes only `.response`; 6.x adds `.code`. Read
    both — keying on `.code` alone silently classifies every 503 as
    non-retryable on the version we actually run.
    """
    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code


def sheets_call(fn, *args, **kwargs):
    """Call a gspread method, retrying transient failures with exponential backoff."""
    for attempt in range(1, SHEETS_RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            status = _sheets_status(e)
            if status not in RETRYABLE_SHEETS_STATUS or attempt == SHEETS_RETRY_ATTEMPTS:
                raise
            reason = f"HTTP {status}"
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if attempt == SHEETS_RETRY_ATTEMPTS:
                raise
            reason = type(e).__name__
        pause = SHEETS_RETRY_BASE_S * (2 ** (attempt - 1))
        print(f"  Sheets {getattr(fn, '__name__', fn)} failed ({reason}) — "
              f"retry {attempt}/{SHEETS_RETRY_ATTEMPTS - 1} in {pause}s")
        time.sleep(pause)


def get_google_client() -> gspread.Client:
    """
    Initialize and return the Google Sheets client.
    """
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    
    if google_creds_json:
        # Running in GitHub Actions
        creds_dict = json.loads(google_creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        # Running locally
        creds = Credentials.from_service_account_file('credentials.json', scopes=scopes)
    
    return gspread.authorize(creds)


# ── Sheet names ─────────────────────────────────────────────────────────────
# Historical deals → sheet1 (the first/default tab, named "Historical")
# Daily deals     → "Daily" tab (see scraper_daily.py)


def get_spreadsheet_id() -> str:
    """
    Get the Google Spreadsheet ID from environment variable.
    Raises an error if not found.
    """
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError(
            "Environment variable 'SPREADSHEET_ID' is not set. "
            "Please set it before running the script."
        )
    return spreadsheet_id


def fetch_existing_rows(worksheet) -> list:
    """
    Fetch existing data from the Google Sheet and ensure every row has 13 columns.
    """
    existing_rows = sheets_call(worksheet.get_all_values)
    print(f"Found {len(existing_rows)} rows in the Google Sheet (including header)")
    
    updated_existing_rows = []
    
    if len(existing_rows) > 1:  # If the sheet has more than just headers
        for row in existing_rows[1:]:
            row_list = list(row)
            
            # If a row has fewer than 13 columns (missing the score), calculate and append it
            if len(row_list) < 13:
                # Calculate score using named fields
                score = calculate_score(
                    row_list[2] if len(row_list) > 2 else '',  # MSRP
                    row_list[6] if len(row_list) > 6 else '',  # Monthly Payment
                    row_list[7] if len(row_list) > 7 else '',  # DAS
                    row_list[4] if len(row_list) > 4 else ''   # Months
                )
                row_list.append(score)
            
            # Ensure row has exactly 13 elements
            while len(row_list) < 13:
                row_list.append('')
            
            # Truncate if more than 13
            row_list = row_list[:13]
            
            if len(row_list) == 13:
                updated_existing_rows.append(row_list)
    
    return updated_existing_rows


def parse_deal_card(card) -> Optional[LeaseDeal]:
    """
    Parse a single deal card and return a LeaseDeal dataclass.
    """
    try:
        # Extract basic text fields
        make = card.select_one('.make_val').text.strip() if card.select_one('.make_val') else ''
        model = card.select_one('.model_val').text.strip() if card.select_one('.model_val') else ''
        model_year = card.select_one('.model_yr_val').text.strip() if card.select_one('.model_yr_val') else ''
        trim = card.select_one('.trim_val').text.strip() if card.select_one('.trim_val') else ''
        msrp = card.select_one('.msrp_val').text.strip() if card.select_one('.msrp_val') else ''
        monthly_payment = card.select_one('.monthly_val').text.strip() if card.select_one('.monthly_val') else ''
        due_at_signing = card.select_one('.das_val').text.strip() if card.select_one('.das_val') else ''
        term_months = card.select_one('.term_val').text.strip() if card.select_one('.term_val') else ''
        miles_per_year = card.select_one('.mileage_val').text.strip() if card.select_one('.mileage_val') else ''
        
        # Extract fields from calculator URL
        calc_link = card.select_one('.calc_val')
        one_pay = False
        sales_price = ''
        mf = ''
        resP = ''
        sales_tax = ''
        
        if calc_link:
            href = calc_link.get('href', '')
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            sales_price = params.get('sales_price', [''])[0]
            mf = params.get('mf', [''])[0]
            resP = params.get('resP', [''])[0]
            sales_tax = params.get('sales_tax', [''])[0]
            one_pay = params.get('onepay', [''])[0] == 'true'
        
        # Calculate Interest Rate % = MF * 2400
        interest_rate = ''
        if mf:
            try:
                interest_rate = round(float(mf) * 2400, 2)
            except ValueError:
                interest_rate = ''
        
        # Build the LeaseDeal dataclass
        deal = LeaseDeal(
            make=make,
            model=f"{model_year} {make} {model} {trim}".strip(),
            msrp=msrp,
            sales_price=sales_price,
            months=term_months,
            miles_per_year=miles_per_year,
            monthly_payment=monthly_payment,
            due_at_signing=due_at_signing,
            sales_tax=sales_tax,
            money_factor=mf,
            interest_rate=str(interest_rate),
            residual_percent=resP,
            one_pay=one_pay
        )
        
        # Calculate score
        deal.score = calculate_score(
            deal.msrp,
            deal.monthly_payment,
            deal.due_at_signing,
            deal.months
        )
        
        return deal
        
    except Exception as e:
        print(f"Error processing card: {e}")
        return None


def scrape_deals() -> list:
    """
    Fetch every regional board and scrape all deals across them.

    `/` is geo-routed to the visitor's own region, so scraping it would capture
    only whatever region the runner's IP lands in (see fetcher.py). The board is
    the union of the seven regions, deduplicated by signature.
    """
    print(f"Fetching {len(REGIONS)} regional boards from pnd.leasehackr.com ...")
    # Tiered fetch per region: requests → Lightpanda → scrapling (see
    # fetcher.py). Raises if any region fails every tier, so a broken fetch
    # fails the workflow loudly instead of writing a partial sheet.
    boards = fetch_all_regions()

    deals = []
    seen = set()
    for region, html_content in boards.items():
        soup = BeautifulSoup(html_content, 'html.parser')
        deal_cards = soup.find_all('div', class_='deal_card')

        added = 0
        for card in deal_cards:
            deal = parse_deal_card(card)
            if deal and deal.signature not in seen:
                seen.add(deal.signature)
                deals.append(deal)
                added += 1
        print(f"  {region:<13} {len(deal_cards):3} cards -> {added} new")

    print(f"Found {len(deals)} unique deal cards across {len(boards)} regions")
    if not deals:
        # Every region fetched cleanly but listed nothing. That is a real (if
        # unusual) state of the board, NOT a scrape failure — don't raise.
        print("WARNING: every region fetched OK but the board is empty today")
    return deals


def filter_new_deals(all_deals: list, existing_rows: list) -> list:
    """
    Filter new deals to remove duplicates from existing sheet.
    """
    # Create a set of signatures from existing rows
    seen_deals = set()
    for row in existing_rows:
        if len(row) >= 7:
            signature = (str(row[0]).strip(), str(row[1]).strip(), str(row[2]).strip(), str(row[6]).strip())
            seen_deals.add(signature)

    # Filter new deals
    new_deals = []
    for deal in all_deals:
        if deal.signature not in seen_deals:
            new_deals.append(deal)
            seen_deals.add(deal.signature)

    return new_deals


def filter_hot_deals(new_deals: list, threshold: float = TELEGRAM_ALERT_THRESHOLD) -> list:
    """Return brand-new deals scoring at or above the alert threshold."""
    return [deal for deal in new_deals if deal.score >= threshold]


def combine_and_deduplicate(existing_rows: list, new_deals: list) -> list:
    """
    Combine existing rows and new deals, deduplicate, and sort by Score.
    """
    # Create a combined list of all deals (existing + new)
    all_deals = []

    # Add existing rows (convert to list if they're LeaseDeal objects)
    for row in existing_rows:
        all_deals.append(row)

    # Add new deals (they're already deduplicated against existing)
    for deal in new_deals:
        all_deals.append(deal.to_list())

    # Deduplicate based on signature
    seen_signatures = set()
    deduplicated_all_deals = []
    for deal in all_deals:
        # Handle both list and LeaseDeal objects
        if hasattr(deal, 'signature'):
            signature = deal.signature
        else:
            signature = (str(deal[0]).strip(), str(deal[1]).strip(), str(deal[2]).strip(), str(deal[6]).strip())
        
        if signature not in seen_signatures:
            seen_signatures.add(signature)
            deduplicated_all_deals.append(deal)

    # Sort by Score (index 12) descending
    deduplicated_all_deals.sort(
        key=lambda x: float(x[12]) if (hasattr(x, '__getitem__') and x[12]) else 0,
        reverse=True
    )

    return deduplicated_all_deals


def get_top_5(all_deals: list) -> list:
    """Get the top 5 deals from the sorted list."""
    return all_deals[:5]


def main():
    """Main function to run the scraper."""
    print("Loading credentials and connecting to Google Sheets...")
    
    # Initialize Google client
    client = get_google_client()
    
    # Get spreadsheet ID from environment variable
    spreadsheet_id = get_spreadsheet_id()
    
    # Open the spreadsheet
    spreadsheet = sheets_call(client.open_by_key, spreadsheet_id)
    worksheet = sheets_call(lambda: spreadsheet.sheet1)

    # Define headers (13 columns including Score)
    headers = [
        'Make', 'Model', 'MSRP', 'Sales Price', 'Months', 'Miles/Year',
        'Monthly Payment', 'Due at Signing', 'Sales Tax', 'Money Factor',
        'Interest Rate %', 'Residual %', 'Score'
    ]

    # Fetch existing data from the sheet
    existing_rows = fetch_existing_rows(worksheet)
    print(f"Processed {len(existing_rows)} existing rows with Score column")

    # Fetch the live page and scrape deals
    scraped_deals = scrape_deals()

    # Convert deals to list format
    list_of_lists = [deal.to_list() for deal in scraped_deals]

    # Print first 3 deals for verification
    print("\n=== First 3 Extracted Deals ===\n")
    for i, deal in enumerate(scraped_deals[:3], 1):
        print(f"Deal {i}: {deal.make} {deal.model} - Score: {deal.score}")

    # Filter new deals to remove duplicates from existing sheet
    new_deals = filter_new_deals(scraped_deals, existing_rows)
    print(f"\nFound {len(new_deals)} NEW deals out of {len(scraped_deals)} scraped")

    # Combine all deals, deduplicate, and sort by Score
    all_deals = combine_and_deduplicate(existing_rows, new_deals)
    print(f"Total unique deals after combine/dedup/sort: {len(all_deals)}")

    # Get Top 5 deals
    top_5 = get_top_5(all_deals)

    print("\n=== Current Top 5 Deals ===\n")
    for i, deal in enumerate(top_5, 1):
        score = deal[12] if hasattr(deal, '__getitem__') else deal.score
        make = deal[0] if hasattr(deal, '__getitem__') else deal.make
        model = deal[1] if hasattr(deal, '__getitem__') else deal.model
        monthly = deal[6] if hasattr(deal, '__getitem__') else deal.monthly_payment
        print(f"{i}. Score: {score}/100 - {make} {model} - ${monthly}/mo")

    # Telegram alert: brand-new deals (first time seen) scoring >= threshold
    hot_new_deals = filter_hot_deals(new_deals)
    print(f"\n[Alert Check] {len(hot_new_deals)} new deal(s) scoring ≥ {TELEGRAM_ALERT_THRESHOLD}")
    if hot_new_deals:
        send_telegram_alert(hot_new_deals)
    else:
        print("  No new deals met the alert threshold — no Telegram message sent.")

    # Rewrite the Sheet - Clear and Write Sorted Data
    print("\nRewriting Google Sheet with sorted deals...")
    sheets_call(worksheet.clear)
    sheets_call(worksheet.append_row, headers)

    if all_deals:
        sheets_call(worksheet.append_rows, all_deals)
    
    print(f"Successfully refreshed the dashboard with {len(all_deals)} sorted deals!")


if __name__ == "__main__":
    main()

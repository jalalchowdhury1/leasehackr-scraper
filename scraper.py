#!/usr/bin/env python3
"""
Scraper script to extract lease deals from leasehackr.com and push to Google Sheets.
Uses scrapling to fetch the page and gspread to write to Google Sheets.
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


def send_telegram_alert(new_top_deals: list) -> None:
    """
    Send a Telegram alert when new deals make it into the Top 5.
    """
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("Telegram credentials not found. Skipping alert.")
        return
    
    text = f"🚨 Leasehackr Alert: {len(new_top_deals)} New Top 5 Deals! 🚨\n\n"
    for deal in new_top_deals:
        text += (
            f"🔥 Score: {deal.score}/100\n"
            f"🚗 {deal.make} {deal.model}\n"
            f"💰 ${deal.monthly_payment}/mo (${deal.due_at_signing} DAS)\n\n"
        )
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload)
        print("Telegram alert sent successfully!")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")


def get_google_client() -> gspread.GSpread:
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
    existing_rows = worksheet.get_all_values()
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
            residual_percent=resP
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
    Fetch the live page and scrape all deals.
    """
    print("Fetching https://pnd.leasehackr.com/ ...")
    fetcher = StealthyFetcher()
    page = fetcher.fetch('https://pnd.leasehackr.com/', network_idle=True)

    # Parse the HTML
    html_content = page.html_content
    soup = BeautifulSoup(html_content, 'html.parser')

    # Find all deal cards
    deal_cards = soup.find_all('div', class_='deal_card')
    print(f"Found {len(deal_cards)} deal cards")

    # Extract data from each deal card
    deals = []
    for card in deal_cards:
        deal = parse_deal_card(card)
        if deal:
            deals.append(deal)
    
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


def get_top_new_deals(top_5: list, new_deals: list) -> list:
    """Find which of the top 5 are new deals."""
    new_deals_signatures = set()
    for deal in new_deals:
        new_deals_signatures.add(deal.signature)

    top_new_deals = []
    for deal in top_5:
        if hasattr(deal, 'signature'):
            sig = deal.signature
        else:
            sig = (str(deal[0]).strip(), str(deal[1]).strip(), str(deal[2]).strip(), str(deal[6]).strip())
        
        if sig in new_deals_signatures:
            top_new_deals.append(deal)

    return top_new_deals


def main():
    """Main function to run the scraper."""
    print("Loading credentials and connecting to Google Sheets...")
    
    # Initialize Google client
    client = get_google_client()
    
    # Get spreadsheet ID from environment variable
    spreadsheet_id = get_spreadsheet_id()
    
    # Open the spreadsheet
    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.sheet1

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
    new_deals = filter_new_deals(list_of_lists, existing_rows)
    print(f"\nFound {len(new_deals)} NEW deals out of {len(list_of_lists)} scraped")

    # Combine all deals, deduplicate, and sort by Score
    all_deals = combine_and_deduplicate(existing_rows, scraped_deals)
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

    # Check if any top 5 deals match new_deals
    top_new_deals = get_top_new_deals(top_5, scraped_deals)

    # Send Telegram alert if new deals made it to top 5
    if top_new_deals:
        print(f"\n{len(top_new_deals)} new deal(s) made it to Top 5!")
        send_telegram_alert(top_new_deals)

    # Rewrite the Sheet - Clear and Write Sorted Data
    print("\nRewriting Google Sheet with sorted deals...")
    worksheet.clear()
    worksheet.append_row(headers)

    if all_deals:
        worksheet.append_rows(all_deals)
    
    print(f"Successfully refreshed the dashboard with {len(all_deals)} sorted deals!")


if __name__ == "__main__":
    main()

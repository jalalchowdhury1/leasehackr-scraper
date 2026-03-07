#!/usr/bin/env python3
"""
Scraper script to extract lease deals from leasehackr.com and push to Google Sheets.
Uses scrapling to fetch the page and gspread to write to Google Sheets.
"""

from scrapling import StealthyFetcher
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import os
import json
import requests
from google.oauth2.service_account import Credentials
import gspread


def calculate_score(msrp, monthly, das, months):
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
    except Exception:
        return 0


def send_telegram_alert(new_top_deals):
    """
    Send a Telegram alert when new deals make it into the Top 5.
    """
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("Telegram credentials not found. Skipping alert.")
        return
    
    text = f"🚨 Leasehackr Alert: {len(new_top_deals)} New Top 5 Deals! 🚨\n\n"
    for d in new_top_deals:
        # d[12] is score, d[0] Make, d[1] Model, d[6] Monthly, d[7] DAS
        text += f"🔥 Score: {d[12]}/100\n🚗 {d[0]} {d[1]}\n💰 ${d[6]}/mo (${d[7]} DAS)\n\n"
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload)
        print("Telegram alert sent successfully!")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")


# ============================================================
# Google Sheets Setup
# ============================================================
scopes = ['https://www.googleapis.com/auth/spreadsheets']
google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')
if google_creds_json:
    # Running in GitHub Actions
    creds_dict = json.loads(google_creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
else:
    # Running locally
    creds = Credentials.from_service_account_file('credentials.json', scopes=scopes)
    
client = gspread.authorize(creds)

# Define the scopes for Google Sheets API
print("Loading credentials and connecting to Google Sheets...")

# Open the spreadsheet
spreadsheet = client.open_by_key('1rmvpKHIIc_1QIZbPE7rFSdEeYFqVsH28-O5Fp_UM_ok')
worksheet = spreadsheet.sheet1

# ============================================================
# 1. Define Headers explicitly with 13th column (Score)
# ============================================================
headers = ['Make', 'Model', 'MSRP', 'Sales Price', 'Months', 'Miles/Year', 'Monthly Payment', 'Due at Signing', 'Sales Tax', 'Money Factor', 'Interest Rate %', 'Residual %', 'Score']

# ============================================================
# 2. Fetch existing data from the sheet
# ============================================================
existing_rows = worksheet.get_all_values()

print(f"Found {len(existing_rows)} rows in the Google Sheet (including header)")

# ============================================================
# 3. Process Existing Rows - ensure every row has 13 columns
# ============================================================
updated_existing_rows = []

if len(existing_rows) > 1:  # If the sheet has more than just headers
    for row in existing_rows[1:]:
        row_list = list(row)
        
        # If a row has fewer than 13 columns (missing the score), calculate and append it
        if len(row_list) < 13:
            # Calculate score using MSRP (index 2), Monthly (index 6), DAS (index 7), Months (index 4)
            score = calculate_score(
                row_list[2] if len(row_list) > 2 else '',
                row_list[6] if len(row_list) > 6 else '',
                row_list[7] if len(row_list) > 7 else '',
                row_list[4] if len(row_list) > 4 else ''
            )
            row_list.append(score)
        
        # Ensure row has exactly 13 elements
        while len(row_list) < 13:
            row_list.append('')
        
        # Truncate if more than 13 (shouldn't happen but just in case)
        row_list = row_list[:13]
        
        # Make sure score is at index 12
        if len(row_list) == 13:
            updated_existing_rows.append(row_list)

print(f"Processed {len(updated_existing_rows)} existing rows with Score column")

# ============================================================
# 4. Fetch the live page and scrape deals
# ============================================================
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
        down_payment = card.select_one('.dp_val').text.strip() if card.select_one('.dp_val') else ''
        
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
        
        # Build the deal dictionary with exact keys for Google Sheets
        deal = {
            'Make': make,
            'Model': f"{model_year} {make} {model} {trim}".strip(),
            'MSRP': msrp,
            'Sales Price': sales_price,
            'Months': term_months,
            'Miles/Year': miles_per_year,
            'Monthly Payment': monthly_payment,
            'Due at Signing': due_at_signing,
            'Sales Tax': sales_tax,
            'Money Factor': mf,
            'Interest Rate %': interest_rate,
            'Residual %': resP
        }
        
        deals.append(deal)
        
    except Exception as e:
        print(f"Error processing card: {e}")
        continue

# ============================================================
# 5. Process New Deals - format as 13-element list with Score
# ============================================================
# Order must match: ['Make', 'Model', 'MSRP', 'Sales Price', 'Months', 'Miles/Year', 'Monthly Payment', 'Due at Signing', 'Sales Tax', 'Money Factor', 'Interest Rate %', 'Residual %', 'Score']
column_order = ['Make', 'Model', 'MSRP', 'Sales Price', 'Months', 'Miles/Year', 'Monthly Payment', 'Due at Signing', 'Sales Tax', 'Money Factor', 'Interest Rate %', 'Residual %', 'Score']

# Build list_of_lists with score as 13th column
list_of_lists = []
for deal in deals:
    row = [deal.get(col, '') for col in column_order[:-1]]  # All columns except Score
    # Calculate score: MSRP (index 2), Monthly Payment (index 6), DAS (index 7), Months (index 4)
    score = calculate_score(deal.get('MSRP', ''), deal.get('Monthly Payment', ''), deal.get('Due at Signing', ''), deal.get('Months', ''))
    row.append(score)  # Add score as 13th column
    
    # Ensure exactly 13 elements
    while len(row) < 13:
        row.append('')
    row = row[:13]
    
    list_of_lists.append(row)

# Print first 3 deals for verification
print("\n=== First 3 Extracted Deals ===\n")
for i, deal in enumerate(list_of_lists[:3], 1):
    print(f"Deal {i}: {deal[0]} {deal[1]} - Score: {deal[12]}")

# ============================================================
# 6. Filter new deals to remove duplicates from existing sheet
# ============================================================
# Create a set of signatures from existing rows
seen_deals = set()
for row in updated_existing_rows:
    if len(row) >= 7:
        signature = (str(row[0]).strip(), str(row[1]).strip(), str(row[2]).strip(), str(row[6]).strip())
        seen_deals.add(signature)

# Filter new deals
new_deals = []
for deal in list_of_lists:
    # deal[0]=Make, deal[1]=Model, deal[2]=MSRP, deal[6]=Monthly Payment
    signature = (str(deal[0]).strip(), str(deal[1]).strip(), str(deal[2]).strip(), str(deal[6]).strip())
    
    if signature not in seen_deals:
        new_deals.append(deal)
        seen_deals.add(signature)

print(f"\nFound {len(new_deals)} NEW deals out of {len(list_of_lists)} scraped")

# ============================================================
# 7. Combine all deals, deduplicate, and sort by Score
# ============================================================
# Create a combined list of all deals (existing + new)
all_deals = []

# Add existing rows
for row in updated_existing_rows:
    all_deals.append(row)

# Add new deals (they're already deduplicated against existing)
for deal in new_deals:
    all_deals.append(deal)

# Deduplicate based on signature
seen_signatures = set()
deduplicated_all_deals = []
for deal in all_deals:
    signature = (str(deal[0]).strip(), str(deal[1]).strip(), str(deal[2]).strip(), str(deal[6]).strip())
    if signature not in seen_signatures:
        seen_signatures.add(signature)
        deduplicated_all_deals.append(deal)

# Sort by Score (index 12) descending
deduplicated_all_deals.sort(key=lambda x: float(x[12]) if x[12] else 0, reverse=True)

all_deals = deduplicated_all_deals

print(f"Total unique deals after combine/dedup/sort: {len(all_deals)}")

# ============================================================
# 8. Top 5 Telegram Logic
# ============================================================
# Grab the first 5 elements from the sorted all_deals
top_5 = all_deals[:5]

print("\n=== Current Top 5 Deals ===\n")
for i, deal in enumerate(top_5, 1):
    print(f"{i}. Score: {deal[12]}/100 - {deal[0]} {deal[1]} - ${deal[6]}/mo")

# Create set of signatures from new_deals for comparison
new_deals_signatures = set()
for deal in new_deals:
    sig = (str(deal[0]).strip(), str(deal[1]).strip(), str(deal[2]).strip(), str(deal[6]).strip())
    new_deals_signatures.add(sig)

# Check if any top 5 deals match new_deals
top_new_deals = []
for deal in top_5:
    sig = (str(deal[0]).strip(), str(deal[1]).strip(), str(deal[2]).strip(), str(deal[6]).strip())
    if sig in new_deals_signatures:
        top_new_deals.append(deal)

# Send Telegram alert if new deals made it to top 5
if top_new_deals:
    print(f"\n{len(top_new_deals)} new deal(s) made it to Top 5!")
    send_telegram_alert(top_new_deals)

# ============================================================
# 9. Rewrite the Sheet - Clear and Write Sorted Data
# ============================================================
print("\nRewriting Google Sheet with sorted deals...")
worksheet.clear()
worksheet.append_row(headers)

if all_deals:
    worksheet.append_rows(all_deals)
    
print(f"Successfully refreshed the dashboard with {len(all_deals)} sorted deals!")

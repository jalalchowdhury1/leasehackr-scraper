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
# 1. Fetch existing data from the sheet
# ============================================================
existing_rows = worksheet.get_all_values()

# 2. Create a set of unique deal signatures to track what's already in the sheet
# Signature consists of: (Make, Model, MSRP, Monthly Payment)
seen_deals = set()
if len(existing_rows) > 1:  # If the sheet has more than just headers
    for row in existing_rows[1:]:
        if len(row) >= 7:  # Ensure row has enough columns
            signature = (str(row[0]).strip(), str(row[1]).strip(), str(row[2]).strip(), str(row[6]).strip())
            seen_deals.add(signature)

print(f"Found {len(seen_deals)} existing deals in the Google Sheet")

# Fetch the live page
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

# Convert list of dictionaries to list of lists with Score as the 13th column
# Order must match: ['Make', 'Model', 'MSRP', 'Sales Price', 'Months', 'Miles/Year', 'Monthly Payment', 'Due at Signing', 'Sales Tax', 'Money Factor', 'Interest Rate %', 'Residual %', 'Score']
column_order = ['Make', 'Model', 'MSRP', 'Sales Price', 'Months', 'Miles/Year', 'Monthly Payment', 'Due at Signing', 'Sales Tax', 'Money Factor', 'Interest Rate %', 'Residual %', 'Score']

# Build list_of_lists with score as 13th column
list_of_lists = []
for deal in deals:
    row = [deal.get(col, '') for col in column_order[:-1]]  # All columns except Score
    # Calculate score: MSRP (index 2), Monthly Payment (index 6), DAS (index 7), Months (index 4)
    score = calculate_score(deal.get('MSRP', ''), deal.get('Monthly Payment', ''), deal.get('Due at Signing', ''), deal.get('Months', ''))
    row.append(score)  # Add score as 13th column
    list_of_lists.append(row)

# Print first 3 deals for verification
print("\n=== First 3 Extracted Deals ===\n")
for i, deal in enumerate(deals[:3], 1):
    print(f"Deal {i}:")
    for key, value in deal.items():
        print(f"  {key}: {value}")
    print()

# ============================================================
# 3. Filter our newly scraped list_of_lists to remove duplicates
# ============================================================
new_deals = []
for deal in list_of_lists:
    # deal[0]=Make, deal[1]=Model, deal[2]=MSRP, deal[6]=Monthly Payment
    signature = (str(deal[0]).strip(), str(deal[1]).strip(), str(deal[2]).strip(), str(deal[6]).strip())
    
    if signature not in seen_deals:
        new_deals.append(deal)
        seen_deals.add(signature)  # Add to set to prevent duplicates within the same daily scrape

# 4. Evaluate Top 5 and send Telegram alert if new deals make the cut
# Combine existing rows (with dynamically calculated scores) and new deals
all_deals_for_scoring = []

# Add existing rows with dynamically calculated scores
if len(existing_rows) > 1:  # If the sheet has more than just headers
    for row in existing_rows[1:]:
        if len(row) >= 7:
            # For older rows without the 13th column, calculate score on the fly
            if len(row) >= 12:
                # Row has at least 12 columns, calculate score: MSRP (2), Monthly (6), DAS (7), Months (4)
                score = calculate_score(row[2] if len(row) > 2 else '', 
                                       row[6] if len(row) > 6 else '', 
                                       row[7] if len(row) > 7 else '', 
                                       row[4] if len(row) > 4 else '')
            else:
                score = 0
            # Pad the row to 13 columns if needed
            row_list = list(row)
            while len(row_list) < 13:
                row_list.append('')
            row_list.append(score)
            all_deals_for_scoring.append(row_list)

# Add new deals
for deal in new_deals:
    deal_list = list(deal)
    while len(deal_list) < 13:
        deal_list.append('')
    all_deals_for_scoring.append(deal_list)

# Sort by score (index 12) descending
all_deals_for_scoring.sort(key=lambda x: float(x[12]) if x[12] else 0, reverse=True)

# Get top 5
top_5 = all_deals_for_scoring[:5]

print("\n=== Current Top 5 Deals ===\n")
for i, deal in enumerate(top_5, 1):
    print(f"{i}. Score: {deal[12]}/100 - {deal[0]} {deal[1]} - ${deal[6]}/mo")

# Find new deals that are in the top 5
new_deals_signatures = set()
for deal in new_deals:
    sig = (str(deal[0]).strip(), str(deal[1]).strip(), str(deal[2]).strip(), str(deal[6]).strip())
    new_deals_signatures.add(sig)

new_top_deals = []
for deal in top_5:
    sig = (str(deal[0]).strip(), str(deal[1]).strip(), str(deal[2]).strip(), str(deal[6]).strip())
    if sig in new_deals_signatures:
        new_top_deals.append(deal)

# Send Telegram alert if new deals made it to top 5
if new_top_deals:
    print(f"\n{len(new_top_deals)} new deal(s) made it to Top 5!")
    send_telegram_alert(new_top_deals)

# 5. Append only the new deals
if new_deals:
    print(f"Appending {len(new_deals)} NEW deals to Google Sheets...")
    worksheet.append_rows(new_deals)
    print(f"Successfully appended {len(new_deals)} NEW deals to Google Sheets!")
else:
    print("No new deals found today. The Google Sheet is already up to date!")

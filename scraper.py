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
from google.oauth2.service_account import Credentials
import gspread

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

# Convert list of dictionaries to list of lists
# Order must match: ['Make', 'Model', 'MSRP', 'Sales Price', 'Months', 'Miles/Year', 'Monthly Payment', 'Due at Signing', 'Sales Tax', 'Money Factor', 'Interest Rate %', 'Residual %']
column_order = ['Make', 'Model', 'MSRP', 'Sales Price', 'Months', 'Miles/Year', 'Monthly Payment', 'Due at Signing', 'Sales Tax', 'Money Factor', 'Interest Rate %', 'Residual %']

list_of_lists = [[deal.get(col, '') for col in column_order] for deal in deals]

# Print first 3 deals for verification
print("\n=== First 3 Extracted Deals ===\n")
for i, deal in enumerate(deals[:3], 1):
    print(f"Deal {i}:")
    for key, value in deal.items():
        print(f"  {key}: {value}")
    print()

# Append to Google Sheets
print("Appending data to Google Sheets...")
worksheet.append_rows(list_of_lists)

print(f"Successfully appended {len(list_of_lists)} deals to Google Sheets!")

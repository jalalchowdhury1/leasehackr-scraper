from bs4 import BeautifulSoup

with open("page_source.html", "r", encoding="utf-8") as f:
    html_content = f.read()

soup = BeautifulSoup(html_content, 'html.parser')

# Find the first deal card
deal_cards = soup.find_all('div', class_='deal_card')
print(f"Found {len(deal_cards)} deal cards")

if deal_cards:
    # Get the first deal card and look for calculator links that contain MF and residual
    first_card = deal_cards[0]
    
    # Find the calc_val link that contains MF and residual values
    calc_links = first_card.find_all('a', class_='calc_val')
    print(f"\nFound {len(calc_links)} calculator links")
    
    if calc_links:
        # Get the href which contains the lease parameters
        href = calc_links[0].get('href', '')
        print(f"\n=== Calculator URL ===")
        print(href[:500])
        
        # Parse out the key values from the URL
        from urllib.parse import urlparse, parse_qs
        
        # Extract query params
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        
        print("\n=== Extracted Lease Parameters ===")
        key_params = ['mf', 'resP', 'msrp', 'sales_price', 'months', 'dp', 'miles', 'sales_tax']
        for key in key_params:
            if key in params:
                print(f"{key}: {params[key][0]}")
    
    # Also look for the con_calc_val link (conquest pricing)
    con_links = first_card.find_all('a', class_='con_calc_val')
    if con_links:
        print("\n=== Conquest Calculator URL ===")
        con_href = con_links[0].get('href', '')
        print(con_href[:500])

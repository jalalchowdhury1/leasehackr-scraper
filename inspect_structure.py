#!/usr/bin/env python3
"""
Script to inspect the structure of leasehackr.com deal cards.
Useful for understanding the HTML structure and debugging selectors.
"""

import sys
import argparse
from urllib.parse import urlparse, parse_qs

from bs4 import BeautifulSoup


def inspect_deal_structure(html_file_path: str) -> None:
    """
    Parse the HTML file and inspect deal card structure.
    
    Args:
        html_file_path: Path to the HTML file to parse.
    """
    # Read the HTML file with error handling
    try:
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except FileNotFoundError:
        print(f"Error: File '{html_file_path}' not found.")
        print("Please provide a valid HTML file path using the -f/--file argument.")
        sys.exit(1)
    except IOError as e:
        print(f"Error reading file '{html_file_path}': {e}")
        sys.exit(1)

    # Parse the HTML with error handling
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
    except Exception as e:
        print(f"Error parsing HTML: {e}")
        sys.exit(1)

    # Find the first deal card
    deal_cards = soup.find_all('div', class_='deal_card')
    print(f"Found {len(deal_cards)} deal cards")

    if not deal_cards:
        print("No deal cards found in the HTML file.")
        return

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


def parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments.
    
    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Inspect the structure of leasehackr.com deal cards from an HTML file."
    )
    parser.add_argument(
        '-f', '--file',
        type=str,
        default='page_source.html',
        help='Path to the HTML file to inspect (default: page_source.html)'
    )
    return parser.parse_args()


def main():
    """Main function to run the structure inspector."""
    args = parse_arguments()
    inspect_deal_structure(args.file)


if __name__ == "__main__":
    main()

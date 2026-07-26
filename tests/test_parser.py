"""parse_deal_card against a saved sample of real pnd.leasehackr.com cards.

The fixture (tests/fixtures/deal_cards_sample.html) is three real cards saved
2026-07-25. If Leasehackr changes its markup these tests keep passing (they
pin OUR parser, not the live site) — selector drift shows up as the live
fetch raising in fetcher.fetch_html, not here.
"""

import os

from bs4 import BeautifulSoup

from scraper import parse_deal_card

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'deal_cards_sample.html')


def _cards():
    with open(FIXTURE) as fh:
        soup = BeautifulSoup(fh.read(), 'html.parser')
    return soup.find_all('div', class_='deal_card')


def test_fixture_has_three_cards():
    assert len(_cards()) == 3


def test_all_cards_parse():
    deals = [parse_deal_card(c) for c in _cards()]
    assert all(d is not None for d in deals)


def test_first_card_fields():
    deal = parse_deal_card(_cards()[0])
    assert deal.make == 'INFINITI'
    assert deal.model == '2027 INFINITI QX60 LUXE AWD'  # "{year} {make} {model} {trim}"
    assert deal.msrp == '61640'
    assert deal.monthly_payment == '399'
    assert deal.due_at_signing == '3925'
    assert deal.months == '24'
    assert deal.money_factor == '0.00162'      # from the calc link query string
    assert deal.residual_percent == '73'
    assert deal.interest_rate == '3.89'        # mf * 2400, rounded to 2
    assert deal.score == 88.7


def test_signature_is_make_model_msrp_monthly():
    deal = parse_deal_card(_cards()[0])
    assert deal.signature == ('INFINITI', '2027 INFINITI QX60 LUXE AWD', '61640', '399')


def test_to_list_matches_13_column_layout():
    deal = parse_deal_card(_cards()[0])
    row = deal.to_list()
    assert len(row) == 13
    assert row[0] == deal.make
    assert row[2] == deal.msrp
    assert row[6] == deal.monthly_payment
    assert row[12] == deal.score

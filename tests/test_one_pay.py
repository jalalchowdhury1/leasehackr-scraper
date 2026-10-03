"""One-pay leases: the card shows $0/month because the whole lease is paid once upfront.

Leasehackr lists these with monthly_val = 0, das_val = the single payment, the
DAS sublabel "one-pay", and onepay=true in the calculator link. Before this was
handled the Telegram alert read "$0/mo ($7,589 DAS)" — the fixture is the real
2026-09-12 Tacoma card that did that.
"""

import os

from bs4 import BeautifulSoup

import scraper
import scraper_daily
from scraper import parse_deal_card, payment_line

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def _cards(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return BeautifulSoup(fh.read(), 'html.parser').find_all('div', class_='deal_card')


def _one_pay_deal():
    cards = _cards('one_pay_card.html')
    assert len(cards) == 1
    return parse_deal_card(cards[0])


def test_one_pay_card_is_flagged():
    deal = _one_pay_deal()
    assert deal.one_pay is True
    assert deal.monthly_payment == '0'       # what the site shows — kept as-is in the sheet
    assert deal.due_at_signing == '7589'     # the single upfront payment
    assert deal.months == '24'
    assert deal.score == 100                 # 7589 / 24 = $316/mo on $40,548 = 0.78%


def test_regular_cards_are_not_one_pay():
    assert all(parse_deal_card(c).one_pay is False for c in _cards('deal_cards_sample.html'))


def test_one_pay_payment_line():
    assert payment_line(_one_pay_deal()) == '💰 One-pay: $7,589 upfront (≈$316/mo)'


def test_regular_payment_line_unchanged():
    deal = parse_deal_card(_cards('deal_cards_sample.html')[0])
    assert payment_line(deal) == '💰 $399/mo ($3,925 DAS)'


def test_one_pay_keeps_sheet_row_and_signature():
    # The flag must not become a 14th column or change dedup identity, or every
    # one-pay row already in Historical would come back as a "new" deal.
    deal = _one_pay_deal()
    assert len(deal.to_list()) == 13
    assert deal.signature == ('Toyota', "2026 Toyota Tacoma 2WD SR5 Double Cab 5' Bed AT", '40548', '0')


def _sent_text(monkeypatch, send):
    sent = {}

    class _Resp:
        status_code = 200
        text = ''

    def fake_post(url, json, **kw):
        sent['text'] = json['text']
        return _Resp()

    monkeypatch.delenv('DIGEST_URL', raising=False)  # direct-send path
    monkeypatch.setenv('TELEGRAM_TOKEN', 't')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'c')
    monkeypatch.setattr(scraper.requests, 'post', fake_post)
    send([_one_pay_deal()])
    return sent['text']


def test_both_alerts_describe_one_pay(monkeypatch):
    for send in (scraper.send_telegram_alert, scraper_daily.send_daily_telegram_alert):
        text = _sent_text(monkeypatch, send)
        assert '$0/mo' not in text
        assert 'One-pay: $7,589 upfront (≈$316/mo)' in text

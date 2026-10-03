"""Silent digest hand-off (3 Oct 2026).

Both scrapers hand their alert to health-hub's collector (api/digest) instead of
messaging the chat; the morning card carries a button per item. Historical posts
id `leasenew`, Daily posts id `leasehackr` — two ids because the collector stores
one item per id and a second post with the same id overwrites the first.
If the collector is down or the env is missing, the old direct send runs, but
silently (disable_notification). No network: requests.post is stubbed.
"""

import scraper
import scraper_daily
from scraper import LeaseDeal


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def _deal():
    return LeaseDeal(make='Honda', model='2026 Honda Civic Sport', msrp='$28,000',
                     sales_price='$26,000', months='36', miles_per_year='10000',
                     monthly_payment='199', due_at_signing='$999', sales_tax='7',
                     money_factor='0.0010', interest_rate='2.4', residual_percent='60',
                     score=99.0)


def _stub(monkeypatch, responses, env):
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        return responses.pop(0)

    monkeypatch.setattr(scraper.requests, 'post', post)
    for k in ('DIGEST_URL', 'DIGEST_KEY', 'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID'):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return calls


FULL_ENV = {'DIGEST_URL': 'https://digest.test/api/digest', 'DIGEST_KEY': 'dk',
            'TELEGRAM_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}


def test_daily_goes_to_digest_and_never_messages_the_chat(monkeypatch):
    calls = _stub(monkeypatch, [_Resp(200, {'ok': True, 'id': 'leasehackr'})], FULL_ENV)
    assert scraper_daily.send_daily_telegram_alert([_deal()]) == 'digest'
    assert len(calls) == 1
    url, kw = calls[0]
    assert url == 'https://digest.test/api/digest'
    assert kw['params'] == {'k': 'dk'}
    assert kw['json']['id'] == 'leasehackr'
    assert 'Leasehackr Daily Alert: 1 Deal(s)' in kw['json']['text']
    assert not any('api.telegram.org' in u for u, _ in calls)


def test_historical_uses_its_own_id_so_daily_cannot_overwrite_it(monkeypatch):
    calls = _stub(monkeypatch, [_Resp(200, {'ok': True, 'id': 'leasenew'})], FULL_ENV)
    assert scraper.send_telegram_alert([_deal()]) == 'digest'
    assert calls[0][1]['json']['id'] == 'leasenew'
    assert 'New Deal(s)' in calls[0][1]['json']['text']


def test_collector_refusal_falls_back_to_a_silent_direct_send(monkeypatch):
    calls = _stub(monkeypatch, [_Resp(400, {'ok': False, 'error': 'unknown sender id'}),
                                _Resp(200, {'ok': True})], FULL_ENV)
    assert scraper_daily.send_daily_telegram_alert([_deal()]) == 'sent'
    url, kw = calls[1]
    assert 'api.telegram.org' in url
    assert kw['json']['disable_notification'] is True


def test_collector_exception_falls_back(monkeypatch):
    sent = []

    def post(url, **kw):
        if 'digest' in url:
            raise ConnectionError('down')
        sent.append(kw)
        return _Resp(200)

    _stub(monkeypatch, [], FULL_ENV)
    monkeypatch.setattr(scraper.requests, 'post', post)
    assert scraper.send_telegram_alert([_deal()]) == 'sent'
    assert sent[0]['json']['disable_notification'] is True


def test_missing_digest_env_skips_the_collector(monkeypatch):
    env = {'TELEGRAM_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}
    calls = _stub(monkeypatch, [_Resp(200)], env)
    assert scraper.send_telegram_alert([_deal()]) == 'sent'
    assert len(calls) == 1 and 'api.telegram.org' in calls[0][0]


def test_no_hot_deals_posts_nothing(monkeypatch):
    calls = _stub(monkeypatch, [], FULL_ENV)
    assert scraper_daily.send_daily_telegram_alert([]) == 'nothing'
    assert calls == []

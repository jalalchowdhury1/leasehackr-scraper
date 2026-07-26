"""fetcher — tier ordering, validation, and loud failure. No network calls."""

from unittest import mock

import pytest

import fetcher


GOOD_HTML = '<html><div class="deal_card">x</div></html>'
BAD_HTML = '<html>maintenance page</html>'


def test_valid_requires_marker():
    assert fetcher._valid(GOOD_HTML)
    assert not fetcher._valid(BAD_HTML)
    assert not fetcher._valid(None)


def test_tier1_success_skips_other_tiers(monkeypatch):
    monkeypatch.setattr(fetcher, '_fetch_requests', lambda url: GOOD_HTML)
    monkeypatch.setattr(fetcher, '_fetch_lightpanda',
                        lambda url: pytest.fail('tier 2 should not run'))
    assert fetcher.fetch_html() == GOOD_HTML


def test_falls_through_to_tier2(monkeypatch):
    monkeypatch.setattr(fetcher, '_fetch_requests', lambda url: None)
    monkeypatch.setattr(fetcher, '_fetch_lightpanda', lambda url: GOOD_HTML)
    monkeypatch.setattr(fetcher, '_fetch_scrapling',
                        lambda url: pytest.fail('tier 3 should not run'))
    assert fetcher.fetch_html() == GOOD_HTML


def test_all_tiers_fail_raises(monkeypatch):
    monkeypatch.setattr(fetcher, '_fetch_requests', lambda url: None)
    monkeypatch.setattr(fetcher, '_fetch_lightpanda', lambda url: None)
    monkeypatch.setattr(fetcher, '_fetch_scrapling', lambda url: None)
    with pytest.raises(RuntimeError):
        fetcher.fetch_html()


def test_requests_tier_accepts_page_with_marker(monkeypatch):
    resp = mock.Mock(status_code=200, text=GOOD_HTML)
    monkeypatch.setattr(fetcher.requests, 'get', lambda *a, **k: resp)
    assert fetcher._fetch_requests(fetcher.PND_URL) == GOOD_HTML


def test_requests_tier_rejects_page_without_marker(monkeypatch):
    resp = mock.Mock(status_code=200, text=BAD_HTML)
    monkeypatch.setattr(fetcher.requests, 'get', lambda *a, **k: resp)
    assert fetcher._fetch_requests(fetcher.PND_URL) is None


def test_requests_tier_survives_exceptions(monkeypatch):
    def boom(*a, **k):
        raise fetcher.requests.ConnectionError('nope')
    monkeypatch.setattr(fetcher.requests, 'get', boom)
    assert fetcher._fetch_requests(fetcher.PND_URL) is None


def test_scrapling_tier_skips_cleanly_when_not_installed():
    # scrapling is no longer in requirements — the tier must degrade to None.
    assert fetcher._fetch_scrapling(fetcher.PND_URL) is None


@pytest.mark.parametrize('system,machine,expected', [
    ('Linux', 'x86_64', 'lightpanda-x86_64-linux'),
    ('Linux', 'aarch64', 'lightpanda-aarch64-linux'),
    ('Darwin', 'arm64', 'lightpanda-aarch64-macos'),
    ('Darwin', 'x86_64', 'lightpanda-x86_64-macos'),
    ('Windows', 'AMD64', None),
])
def test_lightpanda_asset_mapping(monkeypatch, system, machine, expected):
    monkeypatch.setattr(fetcher.platform, 'system', lambda: system)
    monkeypatch.setattr(fetcher.platform, 'machine', lambda: machine)
    assert fetcher._lightpanda_asset() == expected

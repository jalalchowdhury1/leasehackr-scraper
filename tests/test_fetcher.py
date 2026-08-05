"""fetcher — tier ordering, validation, and loud failure. No network calls."""

import sys
from unittest import mock

import pytest

import fetcher


# A real PND page always carries the region filter bar, whether or not that
# region currently has any deals. `deal_card` only appears when it does.
PAGE_HTML = '<html><div class="portal_filter region deals ">Northeast</div>'
GOOD_HTML = PAGE_HTML + '<div class="deal_card None">x</div></html>'
EMPTY_REGION_HTML = PAGE_HTML + '</html>'
BAD_HTML = '<html>maintenance page</html>'


def test_valid_requires_page_marker():
    assert fetcher._valid(GOOD_HTML)
    assert not fetcher._valid(BAD_HTML)
    assert not fetcher._valid(None)


def test_valid_accepts_region_page_with_zero_deals():
    """Regression (2026-08-05): a region with no deals is a healthy page, not a
    failed fetch. Validating on `deal_card` made every empty region look like a
    dead source and hard-failed both workflows."""
    assert fetcher._valid(EMPTY_REGION_HTML)


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


def test_scrapling_tier_skips_cleanly_when_not_installed(monkeypatch):
    # Simulate scrapling being absent even if this machine has it installed —
    # a None entry in sys.modules makes `from scrapling import ...` raise
    # ImportError. Without this, the test would launch a REAL Camoufox fetch
    # on old clones that still have scrapling, hanging the suite for minutes.
    monkeypatch.setitem(sys.modules, 'scrapling', None)
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


# --- regional boards (2026-08-05) ------------------------------------------

def test_region_url_uses_the_route_from_the_filter_bar():
    assert fetcher.region_url('Mid-Atlantic') == \
        'https://pnd.leasehackr.com/r/Mid-Atlantic'


def test_regions_match_the_sites_filter_bar():
    assert fetcher.REGIONS == ('California', 'Northeast', 'Mid-Atlantic',
                               'South', 'West', 'Northwest', 'Midwest')


def test_fetch_all_regions_fetches_every_region(monkeypatch):
    seen = []

    def fake(url):
        seen.append(url)
        return GOOD_HTML

    monkeypatch.setattr(fetcher, 'fetch_html', fake)
    out = fetcher.fetch_all_regions()
    assert list(out) == list(fetcher.REGIONS)
    assert seen == [fetcher.region_url(r) for r in fetcher.REGIONS]


def test_fetch_all_regions_keeps_empty_regions(monkeypatch):
    monkeypatch.setattr(fetcher, 'fetch_html', lambda url: EMPTY_REGION_HTML)
    assert list(fetcher.fetch_all_regions()) == list(fetcher.REGIONS)


def test_fetch_all_regions_raises_if_any_region_fails(monkeypatch):
    """A partial national board must not be written to the sheet silently."""
    def fake(url):
        if url.endswith('/South'):
            raise RuntimeError('all tiers failed')
        return GOOD_HTML

    monkeypatch.setattr(fetcher, 'fetch_html', fake)
    with pytest.raises(RuntimeError, match='South'):
        fetcher.fetch_all_regions()

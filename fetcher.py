#!/usr/bin/env python3
"""
Fetch layer for pnd.leasehackr.com with a three-tier fallback chain:

  1. plain HTTP GET via `requests`   — the page is server-rendered, so this is
     all that's normally needed (verified 2026-07-25: full HTML, no UA gating)
  2. Lightpanda headless browser     — downloaded on demand (single static
     binary); covers the site ever becoming JS-rendered
  3. scrapling StealthyFetcher       — the original Camoufox path; only used if
     the package happens to be installed (it is NOT in requirements.txt any
     more); covers the site ever adding bot detection

Since ~2026-08-03 the board is split into seven REGIONS and `/` is geo-routed
to whichever one the *visitor's IP* falls in — so a GitHub runner in Azure
us-east saw only the (empty) Mid-Atlantic board while the same page from a
Rhode Island laptop showed Northeast deals. Scrape `fetch_all_regions()`, never
`/`, or the sheet silently becomes "whatever region the runner landed in".

A fetch only counts as successful if the HTML contains PAGE_MARKER — the region
filter bar, which every real PND page carries whether or not it has deals. It
deliberately does NOT check for `deal_card`: a region with zero deals is a
healthy page, and validating on deals conflated "source is dead" with "nothing
listed today". If every tier fails, fetch_html() raises — the workflow fails
loudly rather than silently emptying the Daily tab.
"""

import os
import platform
import stat
import subprocess
import tempfile

import requests

PND_URL = 'https://pnd.leasehackr.com/'
REGION_URL = 'https://pnd.leasehackr.com/r/{region}'

# The seven region buttons in the site's filter bar (`route="/r/..."`). Order is
# the site's own. There is no "all regions" route — the union IS the board.
REGIONS = ('California', 'Northeast', 'Mid-Atlantic', 'South', 'West',
           'Northwest', 'Midwest')

# Proves we got a real PND page. Present on every region, including ones with
# no deals at all (those pages are ~13KB and contain zero `deal_card`s).
PAGE_MARKER = 'portal_filter region deals'
# Only present when the region actually has listings — used for logging and by
# the browser tiers' wait-selector, never to decide whether a fetch succeeded.
DEAL_MARKER = 'deal_card'
# What the browser tiers wait for. Must be the filter bar, not `.deal_card`:
# waiting on deals timed out (60s, every run) on regions that have none.
WAIT_SELECTOR = '.portal_filter'
REQUEST_ATTEMPTS = 3
REQUEST_TIMEOUT = 30
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36')

LIGHTPANDA_RELEASE = 'https://github.com/lightpanda-io/browser/releases/download/nightly/'


def _valid(html):
    return html is not None and PAGE_MARKER in html


def region_url(region):
    """URL of one regional board, matching the site's own `/r/<Region>` route."""
    return REGION_URL.format(region=region)


def _fetch_requests(url):
    """Tier 1: plain HTTP GET. Returns HTML or None."""
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT,
                                headers={'User-Agent': USER_AGENT})
            if resp.status_code == 200 and _valid(resp.text):
                print(f"requests fetch OK ({len(resp.text)} bytes)")
                return resp.text
            print(f"requests attempt {attempt}/{REQUEST_ATTEMPTS}: "
                  f"status {resp.status_code}, marker "
                  f"{'present' if _valid(resp.text) else 'MISSING'}")
        except requests.RequestException as exc:
            print(f"requests attempt {attempt}/{REQUEST_ATTEMPTS} failed: {exc}")
    return None


def _lightpanda_asset():
    """Map this platform to a Lightpanda release asset name, or None."""
    system = platform.system()
    machine = platform.machine()
    arch = {'arm64': 'aarch64', 'aarch64': 'aarch64', 'x86_64': 'x86_64',
            'AMD64': 'x86_64'}.get(machine)
    if arch is None:
        return None
    if system == 'Linux':
        return f'lightpanda-{arch}-linux'
    if system == 'Darwin':
        return f'lightpanda-{arch}-macos'
    return None


def _lightpanda_binary():
    """Return a path to a runnable Lightpanda binary, downloading if needed."""
    override = os.environ.get('LIGHTPANDA_BIN')
    if override and os.path.exists(override):
        return override
    asset = _lightpanda_asset()
    if asset is None:
        print(f"Lightpanda: unsupported platform {platform.system()}/{platform.machine()}")
        return None
    path = os.path.join(tempfile.gettempdir(), asset)
    if not os.path.exists(path):
        print(f"Lightpanda: downloading {asset} ...")
        resp = requests.get(LIGHTPANDA_RELEASE + asset, timeout=120)
        resp.raise_for_status()
        with open(path, 'wb') as fh:
            fh.write(resp.content)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def _fetch_lightpanda(url):
    """Tier 2: Lightpanda headless browser. Returns HTML or None."""
    try:
        binary = _lightpanda_binary()
        if binary is None:
            return None
        result = subprocess.run(
            [binary, 'fetch', '--dump', 'html',
             '--wait-selector', WAIT_SELECTOR,
             '--terminate-ms', '60000', '--log-level', 'err', url],
            capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and _valid(result.stdout):
            print(f"Lightpanda fetch OK ({len(result.stdout)} bytes)")
            return result.stdout
        print(f"Lightpanda fetch failed: rc={result.returncode} "
              f"stderr={result.stderr.strip()[:200]}")
    except Exception as exc:  # download error, timeout, missing libc, ...
        print(f"Lightpanda fetch failed: {exc}")
    return None


def _fetch_scrapling(url):
    """Tier 3: original Camoufox path, only if scrapling is installed."""
    try:
        from scrapling import StealthyFetcher
    except ImportError:
        print("scrapling not installed — skipping tier 3 "
              "(pip install 'scrapling[all]' + 'scrapling install' to enable)")
        return None
    try:
        page = StealthyFetcher().fetch(url, wait_selector=WAIT_SELECTOR,
                                       timeout=60000)
        html = page.html_content
        if _valid(html):
            print(f"scrapling fetch OK ({len(html)} bytes)")
            return html
        print(f"scrapling fetch returned HTML without the '{PAGE_MARKER}' marker")
    except Exception as exc:
        print(f"scrapling fetch failed: {exc}")
    return None


def fetch_html(url=PND_URL):
    """Fetch the deals page HTML via the fallback chain. Raises if all tiers fail."""
    for tier in (_fetch_requests, _fetch_lightpanda, _fetch_scrapling):
        html = tier(url)
        if html is not None:
            return html
    raise RuntimeError(
        f"All fetch tiers failed for {url}. If every tier fetched a page but the "
        f"'{PAGE_MARKER}' marker was missing, Leasehackr likely changed its "
        "markup — save the HTML and run inspect_structure.py before changing code.")


def fetch_all_regions(regions=REGIONS):
    """Fetch every regional board. Returns an ordered {region: html} dict.

    Raises if ANY region fails: a partial national board written to the sheet
    would look like "those deals are gone" and poison the dedup/history.
    """
    boards = {}
    for region in regions:
        try:
            boards[region] = fetch_html(region_url(region))
        except RuntimeError as exc:
            raise RuntimeError(f"Region '{region}' failed to fetch: {exc}")
    return boards

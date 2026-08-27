"""Google Sheets calls must survive a transient 5xx.

Regression: 2026-08-27, scraper_daily.py died on
`APIError: {'code': 503, 'status': 'UNAVAILABLE'}` raised out of
`spreadsheet.worksheet("Daily")` — a metadata READ, before a single deal was
scraped. The Historical run 30 s later was fine, so the sheet was healthy; the
call simply had no retry around it.
"""
import pytest
import requests
from gspread.exceptions import APIError, WorksheetNotFound

import scraper
import scraper_daily


class FakeResponse:
    """Just enough of requests.Response for gspread to build an APIError."""

    def __init__(self, status):
        self.status_code = status
        self.text = f"HTTP {status}"

    def json(self):
        return {"error": {"code": self.status_code, "status": "UNAVAILABLE"}}


def api_error(status):
    return APIError(FakeResponse(status))


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Backoff sleeps would make the suite take ~30 s."""
    monkeypatch.setattr(scraper.time, "sleep", lambda *_: None)


def flaky(failures, exc_factory, value="done"):
    """A callable that raises `failures` times, then returns `value`."""
    calls = []

    def fn(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) <= failures:
            raise exc_factory()
        return value

    fn.calls = calls
    return fn


class TestSheetsCall:

    def test_retries_a_503_and_returns_the_value(self):
        fn = flaky(2, lambda: api_error(503))
        assert scraper.sheets_call(fn) == "done"
        assert len(fn.calls) == 3

    def test_retries_a_429_rate_limit(self):
        fn = flaky(1, lambda: api_error(429))
        assert scraper.sheets_call(fn) == "done"
        assert len(fn.calls) == 2

    def test_gives_up_after_the_attempt_budget_and_reraises(self):
        fn = flaky(99, lambda: api_error(503))
        with pytest.raises(APIError):
            scraper.sheets_call(fn)
        assert len(fn.calls) == scraper.SHEETS_RETRY_ATTEMPTS

    def test_a_403_fails_fast_without_retrying(self):
        fn = flaky(99, lambda: api_error(403))
        with pytest.raises(APIError):
            scraper.sheets_call(fn)
        assert len(fn.calls) == 1

    def test_a_404_fails_fast_without_retrying(self):
        fn = flaky(99, lambda: api_error(404))
        with pytest.raises(APIError):
            scraper.sheets_call(fn)
        assert len(fn.calls) == 1

    def test_worksheet_not_found_is_never_retried(self):
        """scraper_daily relies on this to create the Daily tab on first run."""
        fn = flaky(99, lambda: WorksheetNotFound("Daily"))
        with pytest.raises(WorksheetNotFound):
            scraper.sheets_call(fn)
        assert len(fn.calls) == 1

    def test_retries_a_dropped_connection(self):
        fn = flaky(2, lambda: requests.exceptions.ConnectionError("reset"))
        assert scraper.sheets_call(fn) == "done"
        assert len(fn.calls) == 3

    def test_passes_arguments_through(self):
        fn = flaky(1, lambda: api_error(503))
        scraper.sheets_call(fn, "Daily", rows=1)
        assert fn.calls[-1] == (("Daily",), {"rows": 1})


class TestTheActualRegression:

    def test_get_daily_worksheet_survives_a_transient_503(self, monkeypatch):
        sentinel = object()
        flaky_worksheet = flaky(1, lambda: api_error(503), value=sentinel)

        class Spreadsheet:
            worksheet = staticmethod(flaky_worksheet)

        monkeypatch.setattr(scraper_daily.gspread.Client, "open_by_key",
                            lambda self, key: Spreadsheet(), raising=False)
        client = object.__new__(scraper_daily.gspread.Client)

        got = scraper_daily.get_daily_worksheet(client, "sheet-id")

        assert got is sentinel
        assert len(flaky_worksheet.calls) == 2

    def test_a_missing_daily_tab_is_still_created(self, monkeypatch):
        created = object()

        class Spreadsheet:
            @staticmethod
            def worksheet(name):
                raise WorksheetNotFound(name)

            @staticmethod
            def add_worksheet(**kwargs):
                return created

        monkeypatch.setattr(scraper_daily.gspread.Client, "open_by_key",
                            lambda self, key: Spreadsheet(), raising=False)
        client = object.__new__(scraper_daily.gspread.Client)

        assert scraper_daily.get_daily_worksheet(client, "sheet-id") is created

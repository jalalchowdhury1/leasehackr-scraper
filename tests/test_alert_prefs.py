"""Alert preferences (10 Oct 2026): no pickup trucks; a car alerts once, again only cheaper."""
import scraper
from scraper import LeaseDeal, is_truck, pick_alerts


def _deal(make="Honda", model="2026 Honda Civic Sport", msrp="30,000", monthly="200",
          das="1,000", months="36", score=99.0):
    return LeaseDeal(make=make, model=model, msrp=msrp, monthly_payment=monthly,
                     due_at_signing=das, months=months, score=score)


def _row(d):
    return d.to_list()


def test_trucks_are_detected():
    assert is_truck(_deal("Toyota", "2026 Toyota Tacoma 4WD TRD Sport Double Cab 5' Bed AT"))
    assert is_truck(_deal("Toyota", "2026 Toyota Tundra 2WD SR5 CrewMax 5.5' Bed"))
    assert is_truck(_deal("Ford", "2026 Ford F-150 XLT"))
    assert is_truck(_deal("Ram", "2026 Ram 1500 Big Horn"))
    assert not is_truck(_deal("Land Rover", "2026 Range Rover Sport"))   # "Ranger" needs a word boundary
    assert not is_truck(_deal("Toyota", "2026 Toyota RAV4 XLE"))
    assert not is_truck(_deal())


def test_truck_never_alerts():
    tacoma = _deal("Toyota", "2026 Toyota Tacoma 2WD SR5 Double Cab 5' Bed AT", score=100)
    assert pick_alerts([tacoma, _deal()], []) == [_deal()]


def test_below_threshold_never_alerts():
    assert pick_alerts([_deal(score=97.9)], []) == []


def test_same_car_relisted_same_or_higher_price_is_quiet():
    old = _deal(monthly="200")
    assert pick_alerts([_deal(monthly="200")], [_row(old)]) == []
    assert pick_alerts([_deal(monthly="215")], [_row(old)]) == []


def test_same_car_cheaper_alerts_again():
    old = _deal(monthly="200")
    cheaper = _deal(monthly="180")
    assert pick_alerts([cheaper], [_row(old)]) == [cheaper]


def test_one_pay_compares_on_effective_monthly():
    old = _deal(monthly="200", das="1,000")                 # 227.78/mo effective
    one_pay_dearer = _deal(monthly="0", das="9,000")        # 250/mo effective
    one_pay_cheaper = _deal(monthly="0", das="7,200")       # 200/mo effective
    assert pick_alerts([one_pay_dearer], [_row(old)]) == []
    assert pick_alerts([one_pay_cheaper], [_row(old)]) == [one_pay_cheaper]


def test_sheet_number_formats_match():
    # Sheets hands numbers back without $ or commas; the key must still match.
    old = _row(_deal(msrp="30,000"))
    old[2] = "30000"
    assert pick_alerts([_deal(msrp="$30,000")], [old]) == []


def test_different_term_is_a_different_car_deal():
    old = _deal(months="36")
    assert pick_alerts([_deal(months="24")], [_row(old)]) == [_deal(months="24")]

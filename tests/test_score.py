"""calculate_score — the 1% rule: 0.8% of MSRP -> 100, 1.8% -> 0, linear, clamped."""

from scraper import calculate_score


def test_point_eight_percent_scores_100():
    # effective monthly 80 on 10,000 MSRP = 0.8%
    assert calculate_score('10000', '80', '0', '36') == 100


def test_one_point_eight_percent_scores_0():
    assert calculate_score('10000', '180', '0', '36') == 0


def test_midpoint_scores_50():
    # 1.3% is halfway between 0.8% and 1.8%
    assert calculate_score('10000', '130', '0', '36') == 50


def test_das_is_spread_over_term():
    # monthly 70 + 360/36 = effective 80 -> same as 0.8%
    assert calculate_score('10000', '70', '360', '36') == 100


def test_dollar_signs_and_commas_accepted():
    assert calculate_score('$10,000', '$80', '$0', '36') == 100


def test_below_point_eight_percent_clamps_at_100():
    assert calculate_score('10000', '10', '0', '36') == 100


def test_bad_input_scores_0():
    assert calculate_score('not-a-number', '80', '0', '36') == 0
    assert calculate_score('10000', '80', '0', '0') == 0     # div by zero months
    assert calculate_score('', '', '', '') == 0

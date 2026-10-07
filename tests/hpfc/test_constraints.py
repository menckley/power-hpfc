import pytest

from hpfc.constraints import check_coverage, independent_constraints
from tests.hpfc.helpers import PRICING, cfg, curve_day_hours, synthetic_settlements


def _cs(settle=None):
    c = cfg()
    return independent_constraints(synthetic_settlements() if settle is None else settle,
                                   curve_day_hours(), c.curve_start(PRICING), c.curve_end(PRICING),
                                   c.arbitrage_tol)


def test_couverture_complete():
    c = cfg()
    assert len(synthetic_settlements()) == 65
    assert check_coverage(synthetic_settlements(), c.curve_start(PRICING), c.curve_end(PRICING)) == \
        {"Base": 1183, "Peak": 845}


def test_structure_des_contraintes():
    cs = _cs()
    assert len(cs.segments) == 33 and len(cs.unknowns) == 59
    assert len(cs.b_eq) == 58 and cs.degrees_of_freedom == 1
    assert list(zip(cs.dropped["profile"], cs.dropped["name"])) == [
        ("Base", "WE 10/10-11/10/26"), ("Base", "W 12/10-18/10/26"), ("Peak", "W 12/10-18/10/26"),
        ("Base", "Q1-27"), ("Peak", "Q1-27"), ("Base", "Cal-27"), ("Peak", "Cal-27")]
    assert cs.dropped["gap"].abs().max() <= cfg().arbitrage_tol


def test_arbitrage_detecte():
    settle = synthetic_settlements().copy()
    settle.loc[(settle["name"] == "Cal-27") & (settle["profile"] == "Base"), "settlement"] += 1.0
    with pytest.raises(ValueError, match="Arbitrage"):
        _cs(settle)

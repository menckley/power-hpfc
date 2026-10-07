from functools import lru_cache

from hpfc.calibration import build_curve, build_system
from hpfc.shape import fit_shape
from hpfc.validation import check_no_arbitrage, monthly_summary
from tests.hpfc.helpers import PRICING, cfg, curve_cal, curve_day_hours, synthetic_history, synthetic_settlements
from tests.hpfc.test_constraints import _cs


@lru_cache
def _model():
    return fit_shape(synthetic_history(), cfg(), PRICING)


def test_forme_comptages_valides_dans_le_notebook():
    info = _model().info
    assert info["calibration_months"] == 71
    assert info["calibration_hours"] == 51887
    assert info["seasonality_years"] == [2019, 2020, 2024, 2025]
    assert info["intra_months"] == 39
    assert info["intra_cells_min_obs"] == 10
    assert info["intra_pooled"] == [(5, "Vendredi")]
    assert info["pont_days"] == 11


def test_courbe_sans_arbitrage():
    cs = _cs()
    system = build_system(curve_cal(), cs, curve_day_hours())
    curve, info = build_curve(_model(), system, cfg())
    assert len(curve) == 28393
    assert info["iterations"] <= cfg().max_iterations and info["convergence"][-1] < cfg().level_tol
    gaps = check_no_arbitrage(curve, curve_cal(), cs.constraints, synthetic_settlements(), cfg().arbitrage_tol)
    assert gaps["max_gap_constraints"] < 1e-6
    nov = synthetic_settlements().query("name == 'M11-26'").set_index("profile")["settlement"]
    summary = monthly_summary(curve, curve_cal()).loc[202611]
    assert abs(summary["base"] - nov["Base"]) < 1e-6 and abs(summary["peak"] - nov["Peak"]) < 1e-6

from datetime import date

from hpfc.calendar import DAY_TYPES, build_day_calendar, period_hours, validate_calendar
from tests.hpfc.helpers import PRICING, cfg, curve_cal


def test_horizon_et_changements_d_heure():
    cal = curve_cal()
    validate_calendar(cal, cfg())
    assert len(cal) == 28393
    hours = cal.groupby("date")["duration_h"].sum()
    assert sorted(hours[hours == 23].index) == [date(2027, 3, 28), date(2028, 3, 26), date(2029, 3, 25)]
    assert sorted(hours[hours == 25].index) == [date(2026, 10, 25), date(2027, 10, 31),
                                                date(2028, 10, 29), date(2029, 10, 28)]


def test_types_de_jour_feries_et_ponts():
    c = cfg()
    dc = build_day_calendar(c, c.curve_start(PRICING), c.curve_end(PRICING))
    assert dc["day_type"].value_counts().reindex(DAY_TYPES).tolist() == [153, 493, 161, 163, 202, 11]
    assert int(dc["is_holiday"].sum()) == 36
    assert [str(d) for d in dc.index[dc["is_bridge"]]] == [
        "2027-05-07", "2027-11-12", "2028-05-26", "2028-08-14", "2029-04-30", "2029-05-07",
        "2029-05-09", "2029-05-11", "2029-11-02", "2029-12-24", "2029-12-31"]


def test_heures_des_contrats_eex():
    cal = curve_cal()
    expected = {2027: (8760, 3132, 5628), 2028: (8784, 3120, 5664), 2029: (8760, 3132, 5628)}
    for y, hours in expected.items():
        assert tuple(period_hours(cal, date(y, 1, 1), date(y, 12, 31)).astype(int)) == hours
    q = [(date(2027, 1, 1), date(2027, 3, 31), (2159, 768, 1391)),
         (date(2027, 4, 1), date(2027, 6, 30), (2184, 780, 1404)),
         (date(2027, 7, 1), date(2027, 9, 30), (2208, 792, 1416)),
         (date(2027, 10, 1), date(2027, 12, 31), (2209, 792, 1417))]
    for s, e, hours in q:
        assert tuple(period_hours(cal, s, e).astype(int)) == hours

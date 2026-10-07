from dataclasses import replace
from datetime import date

import pytest

from hpfc.config import PeakDefinition
from tests.hpfc.helpers import PRICING, cfg


def test_configuration_france():
    c = cfg()
    assert (c.market, c.tz, c.peak.start_hour, c.peak.end_hour) == ("FR", "Europe/Paris", 8, 20)
    assert c.excluded_periods == ((date(2021, 9, 1), date(2023, 6, 30)),)
    assert c.intra_start == date(2023, 7, 1)
    assert c.curve_start(PRICING) == date(2026, 10, 6) and c.curve_end(PRICING) == date(2029, 12, 31)
    assert len(c.source_hash) == 12


def test_configuration_invalide_refusee():
    bad = replace(cfg(), peak=PeakDefinition(20, 8, (0, 1, 2, 3, 4), True))
    with pytest.raises(ValueError):
        bad.validate()

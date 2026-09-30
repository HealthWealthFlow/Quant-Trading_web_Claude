from qsd.taxonomy import REGIME_LABELS, MarketRegime, RegimeBasis, RegimeSuitability, default_regime_profile


def test_four_regimes_with_labels():
    assert [r.value for r in MarketRegime] == ["BULLISH", "BEARISH", "CONSOLIDATION", "CRASH"]
    assert set(REGIME_LABELS) == set(MarketRegime)


def test_default_profile_is_unknown_not_guessed():
    profile = default_regime_profile()
    assert set(profile) == set(MarketRegime)
    for entry in profile.values():
        assert entry["suitability"] is RegimeSuitability.UNKNOWN
        assert entry["basis"] is RegimeBasis.UNKNOWN
        assert entry["confidence"] == 0.0

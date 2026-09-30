import pytest

from evals.scorers import auprc, auroc, brier, ece, sensitivity_specificity, subgroup_gap


def test_known_values():
    y, p = [0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]
    assert auroc(y, p) == pytest.approx(0.75)
    assert auprc(y, p) == pytest.approx((1 / 1 + 2 / 3) / 2 + 0 if False else 0.8333, abs=1e-3)
    assert brier([1, 0], [1.0, 0.0]) == 0 and brier([1, 0], [0.0, 1.0]) == 1
    assert sensitivity_specificity(y, p, 0.5) == (0.5, 1.0)


def test_ece_perfectly_calibrated_and_overconfident():
    assert ece([1, 1, 0, 0], [0.5, 0.5, 0.5, 0.5]) == pytest.approx(0)
    assert ece([0, 0, 0, 0], [0.9, 0.9, 0.9, 0.9]) == pytest.approx(0.9)


def test_subgroup_gap_and_errors():
    g = subgroup_gap([1, 0, 1, 0], [0.9, 0.1, 0.1, 0.9], ["a", "a", "b", "b"], "brier")
    assert g["a"] < g["b"] and g["__max_gap__"] == pytest.approx(g["b"] - g["a"])
    with pytest.raises(ValueError):
        auroc([1, 1], [0.2, 0.3])

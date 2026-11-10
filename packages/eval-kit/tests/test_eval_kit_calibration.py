"""Judge calibration math for CH-17 (issue 137): Cohen's kappa, TPR, and TNR. Offline."""

import pytest
from eval_kit.calibration import CalibrationError, cohen_kappa, tpr_tnr


def test_eval_kit_calibration_kappa_is_one_on_full_agreement() -> None:
    labels = ["pass", "fail", "pass", "fail"]
    assert cohen_kappa(labels, labels) == 1.0


def test_eval_kit_calibration_kappa_matches_a_worked_example() -> None:
    # Observed agreement 0.7; chance 0.5*0.6 + 0.5*0.4 = 0.5; kappa (0.7-0.5)/0.5 = 0.4.
    first = ["pass"] * 5 + ["fail"] * 5
    second = ["pass"] * 4 + ["fail"] + ["pass"] * 2 + ["fail"] * 3
    assert cohen_kappa(first, second) == pytest.approx(0.4)


def test_eval_kit_calibration_tpr_and_tnr_treat_fail_as_the_positive_class() -> None:
    # Two of three `fail` labels caught; three of four `pass` labels kept.
    labels = ["fail", "fail", "fail", "pass", "pass", "pass", "pass"]
    judge = ["fail", "fail", "pass", "pass", "pass", "pass", "fail"]
    tpr, tnr = tpr_tnr(labels, judge)
    assert tpr == pytest.approx(2 / 3)
    assert tnr == pytest.approx(3 / 4)


@pytest.mark.parametrize(
    ("first", "second"),
    [([], []), (["pass"], ["pass", "fail"]), (["pass"], ["maybe"])],
)
def test_eval_kit_calibration_refuses_empty_uneven_or_unknown_labels(
    first: list[str], second: list[str]
) -> None:
    with pytest.raises(CalibrationError):
        cohen_kappa(first, second)


def test_eval_kit_calibration_tpr_tnr_refuses_a_set_without_both_classes() -> None:
    with pytest.raises(CalibrationError, match="no `fail` label"):
        tpr_tnr(["pass", "pass"], ["pass", "fail"])

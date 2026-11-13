"""Judge calibration math for CH-17 (issue 137): agreement between two labelers, and the judge's
TPR and TNR against the human labels. Pure functions; no process, no key.

`fail` is the positive class: TPR is the share of `fail` labels the judge also fails, TNR the
share of `pass` labels the judge also passes. The 0.85 floor is the issue's `suggested:` value.
"""

from collections.abc import Sequence

__all__ = ["LABELS", "TARGET_RATE", "CalibrationError", "cohen_kappa", "tpr_tnr"]

LABELS = ("pass", "fail")
TARGET_RATE = 0.85
"""suggested: the TPR and TNR floor before the judge may gate `sync` (issue 137)."""


class CalibrationError(ValueError):
    """Labels that cannot be compared: empty, uneven, or not `pass` or `fail`."""


def _check(first: Sequence[str], second: Sequence[str]) -> None:
    if not first:
        raise CalibrationError("no labels to compare")
    if len(first) != len(second):
        raise CalibrationError(f"uneven label lists: {len(first)} and {len(second)}")
    unknown = sorted({x for x in (*first, *second) if x not in LABELS})
    if unknown:
        raise CalibrationError(f"unknown labels: {unknown}; expected pass or fail")


def cohen_kappa(first: Sequence[str], second: Sequence[str]) -> float:
    """Cohen's kappa of two labelers over the same cases. 1.0 when chance agreement is total."""
    _check(first, second)
    n = len(first)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / n
    chance = sum((first.count(x) / n) * (second.count(x) / n) for x in LABELS)
    if chance == 1.0:
        return 1.0
    return (observed - chance) / (1.0 - chance)


def tpr_tnr(labels: Sequence[str], judge: Sequence[str]) -> tuple[float, float]:
    """The judge's TPR and TNR against the human labels, with `fail` as the positive class."""
    _check(labels, judge)
    pairs = list(zip(labels, judge, strict=True))
    fails = [j for h, j in pairs if h == "fail"]
    passes = [j for h, j in pairs if h == "pass"]
    if not fails:
        raise CalibrationError("no `fail` label: TPR is undefined")
    if not passes:
        raise CalibrationError("no `pass` label: TNR is undefined")
    return fails.count("fail") / len(fails), passes.count("pass") / len(passes)

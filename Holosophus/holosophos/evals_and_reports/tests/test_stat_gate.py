import math

from evals_and_reports.stat_gate import compare, mcnemar_exact


def test_mcnemar_no_discordant_is_one():
    assert mcnemar_exact(0, 0) == 1.0


def test_mcnemar_symmetric_balanced_not_significant():
    # b == c => maximally non-significant
    assert mcnemar_exact(5, 5) == 1.0


def test_mcnemar_strong_one_sided_is_significant():
    # 0 vs 10 discordant => p = 2 * 0.5^10 ~ 0.00195
    p = mcnemar_exact(0, 10)
    assert p < 0.05
    assert math.isclose(p, 2 * (0.5 ** 10), rel_tol=1e-9)


def test_compare_locks_only_positive_significant():
    # A hits none, B hits all 10 of the same keys => B strictly better, significant
    a = {("litsearch", f"q{i}"): 0 for i in range(10)}
    b = {("litsearch", f"q{i}"): 1 for i in range(10)}
    r = compare(a, b)
    assert r["b_only_a"] == 0 and r["c_only_b"] == 10
    assert r["delta_pp"] == 100.0
    assert r["significant"] is True
    assert r["locked"] is True


def test_compare_does_not_lock_regression():
    # B worse than A => never locked even if significant
    a = {("litsearch", f"q{i}"): 1 for i in range(10)}
    b = {("litsearch", f"q{i}"): 0 for i in range(10)}
    r = compare(a, b)
    assert r["locked"] is False
    assert r["delta_pp"] == -100.0

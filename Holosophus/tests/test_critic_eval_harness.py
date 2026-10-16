"""The replay harness: re-scoring saved output, and not clobbering it.

Both tests here exist because of the same mistake made twice. The comparator was
fixed *after* a live run, and there was no way to see the effect of the fix
except to buy the critic output again -- while a dry run, costing nothing and
repeated constantly, wrote its canned reports straight over the live ones.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import run_critic_eval as rce  # noqa: E402


def _reports(tmp_path: Path) -> Path:
    """A minimal pair of reports shaped like a real F3 run's."""
    reports = [
        {
            "critic": "paper",
            "iteration": 3,
            "claim_cap": "qualified",
            "coverage": {"claims_in": 2, "derived": 2, "unverified": 0},
            "claims": [
                {"claim_id": "tab:1.acc", "claimed": "0.559", "owner": "writer",
                 "source": "mle/results/x.json"},
                {"claim_id": "tab:1.n", "claimed": "2,994", "owner": "writer",
                 "source": "mle/results/x.json"},
            ],
            "findings": [],
        },
        {
            "critic": "analysis",
            "iteration": 3,
            "claim_cap": None,
            "coverage": {"claims_in": 2, "derived": 2, "unverified": 0},
            "derivations": [
                {"claim_id": "tab:1.acc", "derived": "0.0147", "records": ["mle/results/x.json"]},
                {"claim_id": "tab:1.n", "derived": "2994", "records": ["mle/results/x.json"]},
            ],
            "findings": [],
        },
    ]
    path = tmp_path / "reports.json"
    path.write_text(json.dumps(reports), encoding="utf-8")
    return path


def test_rescoring_saved_reports_rebuilds_the_join(tmp_path):
    """The verdict comes back without a single model call, and the comparator
    still manufactures the mismatch it should: 0.559 is not 0.0147. The pair that
    differs only in formatting (2,994 against 2994) must not be reported."""
    outcome = rce.replay_from_reports(rce.load_fixture("f3"), _reports(tmp_path))

    assert outcome["claims"] == 2
    ids = [f.get("claim_id") for f in outcome["verdict"]["findings"]]
    assert "tab:1.acc" in ids
    assert "tab:1.n" not in ids


def test_rescoring_without_a_paper_report_says_so(tmp_path):
    """The paper report is what the claims are rebuilt from; without it there is
    nothing to join against, and the harness has to name that rather than
    produce an empty verdict that reads like a clean run."""
    path = tmp_path / "reports.json"
    path.write_text(json.dumps([{"critic": "analysis", "findings": []}]), encoding="utf-8")

    outcome = rce.replay_from_reports(rce.load_fixture("f3"), path)
    assert "no paper report" in outcome["error"]


def test_a_dry_run_never_writes_over_live_evidence(tmp_path, monkeypatch):
    """A live run costs money and is the record; a dry run is canned and free.
    They must not share an output directory."""
    monkeypatch.setattr(
        sys, "argv",
        ["run_critic_eval.py", "--fixture", "f3", "--dry-run", "--out", str(tmp_path)],
    )
    rce.main()  # exit code reflects the criteria, not where the files went
    assert (tmp_path / "f3-dry" / "score.json").is_file()
    assert not (tmp_path / "f3").exists()


@pytest.mark.parametrize("fixture_name", ["f3", "f4", "f5", "fg"])
def test_every_fixture_declares_where_its_ground_truth_lives(fixture_name):
    fixture = rce.load_fixture(fixture_name)
    assert fixture.ground_truth.is_file(), f"{fixture_name}: {fixture.ground_truth} missing"

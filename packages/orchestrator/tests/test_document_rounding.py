"""The excerpt rounding the documentation agent applies before excerpts reach the prompt."""

from modelforge_orchestrator.nodes.document import excerpt_floats_to_four_sig_figs


def test_excerpt_floats_are_rounded_to_four_significant_figures() -> None:
    excerpts = {
        "profile": {"target_rate": 0.096612296110414, "n_rows": 4000, "dataset": "synthetic"},
        "binning": [{"iv": [0.382686044532221, 12345]}, "NaN"],
    }
    assert excerpt_floats_to_four_sig_figs(excerpts) == {
        "profile": {"target_rate": 0.09661, "n_rows": 4000, "dataset": "synthetic"},
        "binning": [{"iv": [0.3827, 12345]}, "NaN"],
    }

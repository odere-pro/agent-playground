"""The product plan's phase for each backlog issue it covers: epic_id -> phase A to G.

This is the source of truth for which issues each phase of
`docs/plans/2026-11-06-agent-container-product.md` closes.
- A live issue not named here is `later`.
- A done (WD) issue keeps its phase here, so its work still counts toward the phase.
- A parked (WP) issue is the SLM track's and must not be named.
- `tools/check.py` holds the plan's "The issues in each phase" table to this map.
- `tools/progress.py` reports on it.
"""

PHASES = {
    "A": "Close the must-haves",
    "B": "Models and the judge",
    "C": "LangChain first",
    "D": "LangSmith",
    "E": "Measure and show",
    "F": "Template GA",
    "G": "Under load and on rollout",
}

PRODUCT_PHASE = {
    # A. Close the must-haves
    "H-1": "A",
    "CH-1": "A",
    "H-12": "A",
    "H-18": "A",
    "C-5": "A",
    "CH-3": "A",
    "CH-4": "A",
    "CH-6": "A",
    "X-5": "A",
    "CH-14": "A",
    # B. Models and the judge
    "G-1": "B",
    "G-1b": "B",
    "H-3": "B",
    "CH-2": "B",
    "H-4": "B",
    "S-3": "B",
    "M-2": "B",
    "A-2": "B",
    "CH-17": "B",
    "CH-18": "B",
    # C. LangChain first
    "CH-8": "C",
    "CH-9": "C",
    # D. LangSmith
    "CH-10": "D",
    "CH-11": "D",
    "CH-12": "D",
    # E. Measure and show
    "DEC-1": "E",
    "G-2": "E",
    "G-6": "E",
    "H-7": "E",
    "H-6": "E",
    "A-5": "E",
    "H-16": "E",
    "X-3": "E",
    "X-7": "E",
    "CH-13": "E",
    # F. Template GA
    "H-8": "F",
    "H-9": "F",
    "H-10": "F",
    "H-19": "F",
    "H-15": "F",
    "CH-19": "F",
    # G. Under load and on rollout
    "CH-15": "G",
    "CH-16": "G",
    "CH-7": "G",
}

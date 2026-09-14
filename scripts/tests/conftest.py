"""Pytest configuration for the scripts/ test suite.

scripts/ is a flat collection of top-level modules (generate_loop.py,
cities.py, closures.py, ...) that already import each other directly
(e.g. closures.py's `from generate_loop import ...`) -- there's no
package/src-layout here, and adding one just to run tests would be a much
bigger change than this test suite calls for. Checked pytest's own
current docs before picking an approach (docs.pytest.org's Good
Integration Practices / import-modes pages): the recommended
`--import-mode=importlib` is for a proper installable package and does
NOT put anything on sys.path by itself, so it would actually break these
modules' existing flat sibling imports rather than help. The documented
fix for exactly this "flat scripts, no package" shape is this file --
explicitly add scripts/ (the parent of tests/) to sys.path once, here,
so `import generate_loop` etc. works from test files regardless of
where pytest is invoked from.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

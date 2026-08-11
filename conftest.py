"""Put the repo root on sys.path so `import alphaos` works from any directory.

Without this, the tests only passed because pytest happened to be invoked from
the repo root -- the same class of hidden cwd dependency that stopped the
nightly job from running at all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

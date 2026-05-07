"""Allow ``python -m sim.scenarios <name>`` to dispatch to the package CLI.

Python's ``-m`` runner requires a ``__main__`` module on a package; the
runtime falls back to ``__init__.py`` only when invoked as a script (which
isn't what ``-m`` does for packages). We delegate to ``_main`` in the
package ``__init__`` to keep the dispatch logic in one place.
"""
from __future__ import annotations

import sys

from sim.scenarios import _main


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

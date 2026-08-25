"""Entry point: `python3 -m relay_control`, or the file run by path.

Run by path there is no package to be a member of, so the parent directory goes
on the path first and the package is imported absolutely. Both spellings end in
the same `app.main()`.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from relay_control.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

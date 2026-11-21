#!/usr/bin/env python3
"""Run the installed runtime or its adjacent source checkout."""
from pathlib import Path
import os
import sys

checkout = Path(__file__).resolve().parents[3]
if (checkout / 'src' / 'forge5' / '__init__.py').is_file():
    source = str(checkout / 'src')
    sys.path.insert(0, source)
    inherited = os.environ.get('PYTHONPATH')
    os.environ['PYTHONPATH'] = source + (os.pathsep + inherited if inherited else '')
try:
    from forge5.cli import main
except ModuleNotFoundError as exc:
    if exc.name != 'forge5':
        raise
    print('FORGE-5 runtime unavailable. Install the trusted FORGE-5 checkout with: '
          'python3 -m pip install /absolute/path/to/FORGE-5', file=sys.stderr)
    raise SystemExit(2) from exc
raise SystemExit(main())

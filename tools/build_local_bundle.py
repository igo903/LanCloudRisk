#!/usr/bin/env python3
"""Build the stdlib-only launcher from tracked backend and frontend sources."""
import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ['index.html', 'styles.css', 'workbench.css', 'data.js', 'engine.js',
          'app.js', 'lightweight-charts.js', 'lightweight-charts.LICENSE.txt',
          'interactive-chart.js', 'evidence-views.js', 'views.js',
          'local-connector.js', 'research-views.js', 'strategy-views.js', 'record-ui.js', 'user-state.js', 'themes.css', 'risk.css', 'risk-engine.js', 'risk-views.js', 'market-cards.js', 'market-cards.css', 'bootstrap.js']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', default=str(ROOT / 'run_risklens_local.py'))
    args = parser.parse_args()
    source = (ROOT / 'local' / 'server.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    guard = next(node for node in tree.body if isinstance(node, ast.If)
                 and ast.unparse(node.test) == "__name__ == '__main__'")
    assets = {name: (ROOT / 'dist' / name).read_text(encoding='utf-8') for name in ASSETS}
    lines = source.splitlines(keepends=True)
    assignment = 'EMBEDDED_ASSETS = json.loads(' + repr(json.dumps(assets, ensure_ascii=False)) + ')\n\n'
    lines.insert(guard.lineno - 1, assignment)
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(''.join(lines), encoding='utf-8')
    print(str(output))


if __name__ == '__main__':
    main()

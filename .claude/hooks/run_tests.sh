#!/bin/bash
# PostToolUse hook: run the test suite after edits to pipeline source or tests.
# Suite runs in <1s, so this is effectively free. Exit 2 feeds failures back
# to Claude so it fixes them before moving on.
input=$(cat)
file_path=$(echo "$input" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)

case "$file_path" in
  */src/pipeline/*.py|*/tests/*.py) ;;
  *) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0

out=$(uv run --quiet pytest -q 2>&1)
if [ $? -ne 0 ]; then
  echo "Tests failed after editing $file_path:" >&2
  echo "$out" | tail -20 >&2
  exit 2
fi
exit 0

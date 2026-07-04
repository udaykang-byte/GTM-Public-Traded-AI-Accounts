#!/bin/bash
# SessionStart hook: inject current funnel state so every Claude session
# starts knowing where the pipeline stands. Degrades silently when the
# project isn't configured yet.
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" 2>/dev/null || exit 0

if [ ! -f .env ]; then
  echo "AIPT pipeline: .env not configured yet (copy .env.example -> .env)."
  exit 0
fi

uv run --quiet python -m pipeline status --brief 2>/dev/null \
  || echo "AIPT pipeline: status unavailable (run 'uv sync', check Supabase creds in .env)."
exit 0

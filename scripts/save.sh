#!/usr/bin/env bash
# Saves the agents' results back to the repository. Safe to run when nothing changed.
set -u
git config user.name "job-agents[bot]"
git config user.email "job-agents@users.noreply.github.com"
git add -A data outbox README.md docs
if git diff --cached --quiet; then
  echo "Nothing to save."
  exit 0
fi
git commit -q -m "agents: update $(date -u '+%Y-%m-%d %H:%M')"
for i in 1 2 3 4 5; do
  if git push -q; then
    echo "Saved."
    exit 0
  fi
  echo "Push failed (attempt $i), merging and retrying..."
  git pull -q --rebase -X theirs || git rebase --abort 2>/dev/null
  sleep $((i * 3))
done
echo "::warning::Could not save the results to the repository."
exit 1

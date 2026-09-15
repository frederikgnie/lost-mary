#!/usr/bin/env bash
# Stage a plugin-only copy of this repository for `claude plugin eval`.
#
# Why this exists: the eval harness refuses a plugin in which any file has more
# than one name ("a file in the plugin has more than one name (a hard link) -
# case definitions must not be reachable by another name"). It scans the whole
# plugin root and does not honour .gitignore, so a dev `.venv` created by uv -
# which hard-links 475 files out of its cache here - blocks every run from the
# repository root, even though `.venv` is ignored and is not plugin content.
#
# Measured 2026-09-15 on Claude Code 2.1.272: `claude plugin eval .` aborts
# before spending anything; the same suite against a staged copy runs fine.
#
# Usage:
#   ./evals/stage.sh                        # stage, print the path
#   claude plugin eval "$(./evals/stage.sh)" --runs 1 --no-publish
#
# Results land under <staged>/evals/results/, not in the repository.
set -euo pipefail

destination="${1:-${TMPDIR:-/tmp}/lost-mary-plugin}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Plugin content only: the manifest, the roles, the procedures, the hook scripts
# those hooks point at, and the suite. Everything else in the repository - the
# venv, the tests, CI, docs - is not part of what is being evaluated.
parts=(.claude-plugin agents skills hooks scripts evals)

rm -rf "$destination"
mkdir -p "$destination"

for part in "${parts[@]}"; do
    [ -e "$repo_root/$part" ] || { echo "missing plugin part: $repo_root/$part" >&2; exit 1; }
    # --no-preserve=links is GNU-only; fall back to a plain recursive copy, which
    # also breaks the links because it writes new files.
    cp -r --no-preserve=links "$repo_root/$part" "$destination/" 2>/dev/null \
        || cp -r "$repo_root/$part" "$destination/"
done

# Results are run artefacts; a staged copy starts without the previous readings.
rm -rf "$destination/evals/results"

# The check the harness itself makes. Fail here, with the offending paths, rather
# than inside a run that reports it as a scored case failure.
if linked="$(find "$destination" -type f -links +1 -print)" && [ -n "$linked" ]; then
    echo "hard links in staged copy:" >&2
    echo "$linked" >&2
    echo "the eval harness will refuse it" >&2
    exit 1
fi

echo "$destination"

#!/usr/bin/env bash
# Builds dist/keel-<VERSION>.zip from a `git archive` of HEAD
# (so uncommitted files never ship), copies INSTALL.md next to it as the
# single English entry point, and scans both for leaked secrets or the
# author's home path before declaring success.
# This script archives the repo it is run from (via `git archive HEAD`).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

VERSION="$(cat VERSION)"
ZIP="dist/keel-${VERSION}.zip"
GUIDE="dist/INSTALL.md"

# Wiped rather than just removing this run's own two output names, so a
# stale file this script no longer produces (an old version's zip, a
# leftover dist/README.txt, ...) never lingers in dist/.
rm -rf dist
mkdir -p dist
git archive --format=zip --prefix=keel-${VERSION}/ -o "$ZIP" HEAD
# INSTALL.md ships next to the zip as the entry point (also inside the zip
# as part of the archive) — read from HEAD, same as the archive above, so
# it is always byte-identical to the copy inside the zip even if the
# working tree has uncommitted INSTALL.md edits.
git show HEAD:INSTALL.md > "$GUIDE"

# Length-qualified so the pattern text itself never matches this script.
SECRET_PATTERN='ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}|xox[bp]-[0-9A-Za-z-]{10,}|ATATT[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16}'

# Built from pieces so the script does not match its own source.
AUTHOR_HOME="/Users/""jin""kim"

SCAN_DIR="$(mktemp -d)"
HITS_FILE="$(mktemp)"
trap 'rm -rf "$SCAN_DIR" "$HITS_FILE"' EXIT
unzip -q "$ZIP" -d "$SCAN_DIR"
cp "$GUIDE" "$SCAN_DIR/INSTALL.md"

set +e
grep -rElI "$SECRET_PATTERN|$AUTHOR_HOME" "$SCAN_DIR" > "$HITS_FILE" 2>&1
GREP_RC=$?
set -e

if [ "$GREP_RC" -eq 0 ]; then
  echo "make_dist.sh: possible secret or author home path found in:" >&2
  sed "s#^$SCAN_DIR/##" "$HITS_FILE" >&2
  exit 1
elif [ "$GREP_RC" -ne 1 ]; then
  echo "make_dist.sh: secret scan failed to run (grep exit $GREP_RC):" >&2
  cat "$HITS_FILE" >&2
  exit 1
fi

SIZE="$(du -h "$ZIP" | cut -f1)"
GUIDE_SIZE="$(du -h "$GUIDE" | cut -f1)"
echo "$ZIP ($SIZE)"
echo "$GUIDE ($GUIDE_SIZE)"

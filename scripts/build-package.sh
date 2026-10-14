#!/usr/bin/env bash
#
# Build the Lambda deployment package.
#
# The artefact contains `sap_patch_day/` at the archive root (so the handler is
# `sap_patch_day.handler.lambda_handler`) and nothing else: the runtime provides
# boto3, and there are no third-party dependencies at all
# (docs/addenda/0003-runtime-and-dependency-policy.md).
#
# The zip is deterministic: fixed timestamps, sorted entries, so identical
# source produces an identical SHA-256 that a reviewer can compare.
#
# Bash 3.2 compatible (GREENFIELD.md §10.4).
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

DIST_DIR="${REPO_ROOT}/dist"
STAGE_DIR="${DIST_DIR}/stage"
SOURCE_DIR="${REPO_ROOT}/src/sap_patch_day"
ZIP_PATH="${ZIP_PATH:-${DIST_DIR}/sap-security-patch-day-notifier.zip}"

info() { printf '[build-package] %s\n' "$*"; }
die() { printf '[build-package] ERROR: %s\n' "$*" >&2; exit 1; }

[ -d "$SOURCE_DIR" ] || die "source directory not found: ${SOURCE_DIR}"
command -v python3 >/dev/null 2>&1 || die "python3 is required to build the package"

info "staging ${SOURCE_DIR} -> ${STAGE_DIR}/sap_patch_day"
rm -rf "$STAGE_DIR"
mkdir -p "${STAGE_DIR}/sap_patch_day"

# Only the package's own source: no tests, no __pycache__, no fixtures.
find "$SOURCE_DIR" -maxdepth 1 -type f -name '*.py' -exec cp {} "${STAGE_DIR}/sap_patch_day/" \;
[ -n "$(ls -A "${STAGE_DIR}/sap_patch_day")" ] || die "no Python modules found in ${SOURCE_DIR}"

mkdir -p "$DIST_DIR"
rm -f "$ZIP_PATH"

python3 - "$STAGE_DIR" "$ZIP_PATH" <<'PYTHON'
import os
import sys
import zipfile

stage_dir, zip_path = sys.argv[1], sys.argv[2]
FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)

entries = []
for root, _dirs, files in os.walk(stage_dir):
    for name in sorted(files):
        full = os.path.join(root, name)
        entries.append((full, os.path.relpath(full, stage_dir).replace(os.sep, "/")))

with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for full, arcname in sorted(entries, key=lambda item: item[1]):
        info = zipfile.ZipInfo(arcname, date_time=FIXED_TIMESTAMP)
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(full, "rb") as handle:
            archive.writestr(info, handle.read())
PYTHON

rm -rf "$STAGE_DIR"
[ -s "$ZIP_PATH" ] || die "package was not created: ${ZIP_PATH}"

if command -v sha256sum >/dev/null 2>&1; then
  CHECKSUM=$(sha256sum "$ZIP_PATH" | awk '{print $1}')
else
  CHECKSUM=$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')
fi

SIZE_BYTES=$(wc -c < "$ZIP_PATH" | tr -d ' ')
info "package: ${ZIP_PATH}"
info "entries: $(python3 -c 'import sys,zipfile; print(len(zipfile.ZipFile(sys.argv[1]).namelist()))' "$ZIP_PATH")"
info "size:    ${SIZE_BYTES} bytes"
info "sha256:  ${CHECKSUM}"
printf '%s\n' "$ZIP_PATH"

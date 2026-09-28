#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)"
exec python3 "$SCRIPT_DIR/../scripts/build_pdf.py" "$@"

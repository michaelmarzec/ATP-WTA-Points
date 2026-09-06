#!/usr/bin/env bash
# Double-clickable launcher for scripts/refresh_data.sh.
# Lives at the repo root so double-clicking it in Finder works out of the box.
cd "$(dirname "${BASH_SOURCE[0]}")"
./scripts/refresh_data.sh
echo
read -r -p "Press Enter to close this window..."

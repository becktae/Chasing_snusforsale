#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
set -a
source .env
set +a
/usr/bin/env python3 tracker.py

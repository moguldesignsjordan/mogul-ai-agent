#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../apps/api-python"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8787

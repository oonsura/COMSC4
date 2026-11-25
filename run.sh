#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  COMSC4 DEBSIRIN - Starting Server (Linux / Codespaces)"
echo "============================================================"

if ! python3 -c "import django, openpyxl, PIL, cv2" >/dev/null 2>&1; then
    echo "[1/3] Installing requirements..."
    python3 -m pip install -q -r requirements.txt
else
    echo "[1/3] Requirements already satisfied."
fi

echo "[2/3] Applying database migrations..."
python3 manage.py migrate --noinput
python3 -c "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'comsc4.settings'); django.setup(); from core.services import seed_initial_comsc4_data; seed_initial_comsc4_data(); print('Initial COMSC4 data ready!')"

echo "[3/3] Starting COMSC4 server on 0.0.0.0:8000 ..."
python3 manage.py runserver 0.0.0.0:8000

#!/bin/bash
python -m playwright install chromium --with-deps 2>/dev/null || python -m playwright install chromium || true
exec gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120

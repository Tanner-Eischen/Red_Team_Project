#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
chmod 700 /tests /logs/verifier
rm -f /logs/verifier/reward.txt
set +e
python3 /tests/verify.py --workspace /app
status=$?
set -e
if [ "$status" -eq 0 ]; then
    printf '1\n' > /logs/verifier/reward.txt
elif [ "$status" -eq 1 ]; then
    printf '0\n' > /logs/verifier/reward.txt
else
    exit "$status"
fi

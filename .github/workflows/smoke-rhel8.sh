#!/usr/bin/env bash
set -euo pipefail

work=$(mktemp -d)
cd "$work"
test -x /release/case-investigator
sed -e "s|/var/lib/case-investigator|$work/state|" \
    -e 's|prefix: "/case-investigator"|prefix: "/drc/case-investigator"|' \
    -e 's|client_id: ""|client_id: smoke|' \
    -e 's|client_secret: ""|client_secret: smoke|' /release/config.example.yml > config.yml
/release/case-investigator --config config.yml >service.log 2>&1 &
service_pid=$!
trap 'kill "$service_pid" 2>/dev/null || true' EXIT

base=http://127.0.0.1:8000/drc/case-investigator
for attempt in {1..30}; do
    if curl -fsS "$base/" -o index.html 2>/dev/null; then break; fi
    if ! kill -0 "$service_pid" 2>/dev/null; then cat service.log; exit 1; fi
    sleep 1
done
if ! grep -q 'DRC查案助手' index.html; then cat service.log; exit 1; fi
script=$(sed -n 's/.*src="\.\/\(assets\/[^"]*\.js\)".*/\1/p' index.html)
test -n "$script"
curl -fsS "$base/$script" -o app.js
test -s app.js
test "$(curl -sS -o /dev/null -w '%{http_code}' "$base/api/me")" = 401

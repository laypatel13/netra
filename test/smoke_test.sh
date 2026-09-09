#!/usr/bin/env bash
# End-to-end API health check — a scripted version of the curl-based
# verification done ad hoc while building this project. Re-runnable and
# safe to run repeatedly: onboarding/seeding steps tolerate already-seeded
# data instead of failing on it.
#
# Usage:
#   BACKEND_URL=http://127.0.0.1:8010 bash test/smoke_test.sh
#
# Requires: the backend running and reachable at BACKEND_URL, with at
# least one camera already onboarded (run this from the repo root, or set
# SEED_DIR explicitly).
#
# Exits 0 if everything checked out, 1 on the first genuinely unexpected
# result (not on "already exists" / "already onboarded" — those are fine).
set -u

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8010}"
SEED_DIR="${SEED_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/seed_data}"

PASS=0
FAIL=0

# check DESCRIPTION EXPECTED_HTTP_STATUS -- ACTUAL_CURL_ARGS...
# Runs curl, compares the HTTP status to what's expected, prints a
# PASS/FAIL line. Response body is left in $LAST_BODY for the caller to
# inspect further.
check() {
  local desc="$1" expected="$2"
  shift 2
  local tmp
  tmp="$(mktemp)"
  local status
  status="$(curl -s -o "$tmp" -w '%{http_code}' "$@")"
  LAST_BODY="$(cat "$tmp")"
  rm -f "$tmp"
  if [ "$status" = "$expected" ]; then
    echo "PASS  [$status] $desc"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$status, expected $expected] $desc"
    echo "      response: $LAST_BODY"
    FAIL=$((FAIL + 1))
  fi
}

echo "== Backend health =="
check "backend is up" 200 "$BACKEND_URL/"

echo ""
echo "== RBAC =="
check "onboard camera WITHOUT admin role is rejected" 403 \
  -X POST "$BACKEND_URL/cameras" -H "Content-Type: application/json" \
  -d '{"camera_id":"cam-smoketest","latitude":23.0,"longitude":72.5,"department":"Gujarat Police","camera_type":"ip"}'

echo ""
echo "== Registry: bulk CSV onboarding (idempotent — skips already-onboarded cameras) =="
check "bulk CSV onboarding" 200 \
  -X POST "$BACKEND_URL/cameras/bulk-csv" -H "X-Role: admin" \
  -F "file=@$SEED_DIR/cameras_seed.csv"

echo ""
echo "== Gap analysis =="
check "gap-analysis report loads" 200 "$BACKEND_URL/cameras/gap-analysis"
TOTAL_CAMERAS="$(echo "$LAST_BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["summary"]["total_cameras"])' 2>/dev/null || echo 0)"
if [ "$TOTAL_CAMERAS" -gt 0 ] 2>/dev/null; then
  echo "PASS  registry has $TOTAL_CAMERAS camera(s)"
  PASS=$((PASS + 1))
else
  echo "FAIL  registry reports 0 cameras — did bulk-csv onboarding actually run?"
  FAIL=$((FAIL + 1))
fi

echo ""
echo "== Watchlist: seed representative entries (idempotent — 409 on already-seeded plates is fine) =="
python3 - "$SEED_DIR/watchlist_seed.json" "$BACKEND_URL" <<'PYEOF'
import json, sys, urllib.request

path, backend = sys.argv[1], sys.argv[2]
entries = json.load(open(path))
for entry in entries:
    req = urllib.request.Request(
        f"{backend}/watchlist",
        data=json.dumps(entry).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"PASS  seeded watchlist entry: {entry.get('plate_number') or entry.get('vehicle_color') + ' ' + entry.get('vehicle_type')} ({resp.status})")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print(f"PASS  watchlist entry already seeded: {entry.get('plate_number') or entry.get('vehicle_color') + ' ' + entry.get('vehicle_type')}")
        else:
            print(f"FAIL  seeding watchlist entry failed ({e.code}): {e.read().decode()}")
            sys.exit(1)
PYEOF
if [ $? -eq 0 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi

echo ""
echo "== Detections: record with a plate that matches a seeded watchlist entry =="
CAM_ID="$(curl -s "$BACKEND_URL/cameras" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["camera_id"])')"
THUMB="$(mktemp /tmp/smoketest_thumb.XXXX.jpg)"
python3 -c "
import cv2, numpy as np
img = np.zeros((40, 40, 3), dtype='uint8')
img[:] = (0, 0, 200)
cv2.imwrite('$THUMB', img)
" 2>/dev/null || { echo "SKIP  cv2 not available in this environment — can't generate a thumbnail, skipping detection checks"; THUMB=""; }

if [ -n "$THUMB" ]; then
  check "record a plate-matching detection (cam=$CAM_ID)" 200 \
    -X POST "$BACKEND_URL/detections" \
    -F "camera_id=$CAM_ID" -F "timestamp_ms=1000" -F "confidence=0.9" \
    -F "plate_number=GJ01AB1234" -F "thumbnail=@$THUMB;type=image/jpeg"
  DETECTION_ID="$(echo "$LAST_BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null)"

  echo ""
  echo "== Detections: record an attribute-only detection matching a seeded watchlist entry =="
  check "record a silver_gray car detection, no plate (cam=$CAM_ID)" 200 \
    -X POST "$BACKEND_URL/detections" \
    -F "camera_id=$CAM_ID" -F "timestamp_ms=2000" -F "confidence=0.6" \
    -F "vehicle_type=car" -F "vehicle_color=silver_gray" -F "thumbnail=@$THUMB;type=image/jpeg"

  rm -f "$THUMB"

  echo ""
  echo "== Thumbnail serving =="
  if [ -n "$DETECTION_ID" ]; then
    check "fetch the thumbnail we just uploaded" 200 "$BACKEND_URL/detections/$DETECTION_ID/thumbnail"
  fi

  echo ""
  echo "== Watchlist alerts feed (should now show both the exact-plate and attribute matches) =="
  check "recent alerts" 200 "$BACKEND_URL/watchlist/alerts/recent"
  TIERS="$(echo "$LAST_BODY" | python3 -c 'import json,sys; print([a["tier"] for a in json.load(sys.stdin)])' 2>/dev/null)"
  echo "      tiers seen: $TIERS"
  if echo "$TIERS" | grep -q "exact_plate" && echo "$TIERS" | grep -q "attributes"; then
    echo "PASS  both alert tiers present"
    PASS=$((PASS + 1))
  else
    echo "FAIL  expected both 'exact_plate' and 'attributes' tiers in the alerts feed"
    FAIL=$((FAIL + 1))
  fi

  echo ""
  echo "== Attribute search =="
  check "search by type+color finds the detection" 200 \
    "$BACKEND_URL/detections/search?vehicle_type=car&vehicle_color=silver_gray"
  check "search with a since= in the far future finds nothing" 404 \
    "$BACKEND_URL/detections/search?vehicle_type=car&vehicle_color=silver_gray&since=2099-01-01T00:00:00"

  echo ""
  echo "== Delete endpoints =="
  check "delete a camera WITH recorded detections is refused" 409 \
    -X DELETE "$BACKEND_URL/cameras/$CAM_ID" -H "X-Role: admin"
fi

echo ""
echo "======================================"
echo "  $PASS passed, $FAIL failed"
echo "======================================"
[ "$FAIL" -eq 0 ]

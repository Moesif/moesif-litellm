#!/bin/bash
# Proxy mode e2e tests — requires proxy running on localhost:4000
# Usage: bash tests/e2e/proxy_test.sh

PROXY_URL="http://localhost:4000"
PASS=0
FAIL=0
BODY_FILE=$(mktemp)

do_curl() {
    STATUS=$(curl -s -o "$BODY_FILE" -w "%{http_code}" "$@")
    BODY=$(cat "$BODY_FILE")
}

check() {
    local label="$1"
    if echo "$BODY" | grep -q '"choices"'; then
        echo "  PASS  $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $label (HTTP $STATUS)"
        echo "        $(echo "$BODY" | cut -c1-200)"
        FAIL=$((FAIL + 1))
    fi
}

echo ""
echo "── Proxy Mode Tests ────────────────────────────────────"
echo "  Target: $PROXY_URL"
echo "────────────────────────────────────────────────────────"
echo ""

# 1. Basic request — user and company via metadata
echo "1. Basic request (user + company from metadata)"
do_curl "$PROXY_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-flash",
    "messages": [{"role": "user", "content": "Say hello in one word"}],
    "user": "alice",
    "metadata": {"company_id": "acme-corp"}
  }'
check "user=alice company=acme-corp"

sleep 2

# 2. No user or company — should still succeed, identities will be null
echo "2. Request without user or company"
do_curl "$PROXY_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-flash",
    "messages": [{"role": "user", "content": "Say hello in one word"}]
  }'
check "no user or company"

sleep 2

# 3. Different user and company
echo "3. Different user and company"
do_curl "$PROXY_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-flash",
    "messages": [{"role": "user", "content": "Say hello in one word"}],
    "user": "bob",
    "metadata": {"company_id": "wso2"}
  }'
check "user=bob company=wso2"

sleep 2

# 4. Invalid model — should return error
echo "4. Invalid model (expect error)"
do_curl "$PROXY_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nonexistent-model",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
if echo "$BODY" | grep -qE '"error"|"detail"'; then
    echo "  PASS  invalid model returns error (HTTP $STATUS)"
    PASS=$((PASS + 1))
else
    echo "  FAIL  expected error for invalid model"
    FAIL=$((FAIL + 1))
fi

rm -f "$BODY_FILE"

echo ""
echo "────────────────────────────────────────────────────────"
echo "  Results: $PASS passed, $FAIL failed"
echo "────────────────────────────────────────────────────────"
echo ""
echo "Check Moesif dashboard to verify events landed with correct user_id and company_id."
echo ""

[ $FAIL -eq 0 ] && exit 0 || exit 1
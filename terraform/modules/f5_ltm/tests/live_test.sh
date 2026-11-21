#!/usr/bin/env bash
# Live test of the f5_ltm module against a real BIG-IP, with isolated objects
# (tftest-*, 198.18.0.0/15, virtuals disabled) that are destroyed at the end.
#
#   F5_ADDRESS=10.10.10.121 F5_USERNAME=root F5_PASSWORD=... ./live_test.sh
#
# 1. apply tftest-v1.yml (create)          -> box matches, re-plan is empty
# 2. apply tftest-v2.yml (day-2 edit)      -> box matches, deleted objects gone,
#                                             re-plan is empty
# 3. destroy                               -> every tftest object gone
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
py="${PYTHON:-python3}"
: "${F5_ADDRESS:?}" "${F5_USERNAME:?}" "${F5_PASSWORD:?}"
export TF_VAR_bigip_address="$F5_ADDRESS" TF_VAR_bigip_username="$F5_USERNAME" TF_VAR_bigip_password="$F5_PASSWORD"
export TF_IN_AUTOMATION=1
tf() { terraform -chdir="$here/live" "$@" -no-color -input=false; }

v1="$here/fixtures/tftest-v1.yml"
v2="$here/fixtures/tftest-v2.yml"
all_absent=(virtual:/Common/tftest-vs-https virtual:/Common/tftest-vs-l4
            pool:/Common/tftest-web pool:/Common/tftest-l4 snatpool:/Common/tftest-snat
            monitor/http:/Common/tftest-hc-http node:/Common/198.18.0.10 node:/Common/198.18.0.11)

cleanup() { echo "--- cleanup: destroy"; tf destroy -auto-approve -var config_file="$v1" >/dev/null || true; }
trap 'echo "FAILED at line $LINENO"; cleanup' ERR

no_drift() {
  set +e; tf plan -detailed-exitcode -var config_file="$1" >"$here/live/drift.log" 2>&1; rc=$?; set -e
  if [ $rc -ne 0 ]; then echo "re-plan after apply is not empty (exit $rc):"; grep -E '^\s+[~+-]|Plan:' "$here/live/drift.log"; return 1; fi
  echo "re-plan: no changes"
}

tf init >/dev/null
echo "--- step 1: create from tftest-v1.yml"
tf apply -auto-approve -var config_file="$v1" | grep -E 'Apply complete|Error' 
"$py" "$here/verify.py" "$v1"
no_drift "$v1"

echo "--- step 2: day-2 edit, tftest-v2.yml"
tf apply -auto-approve -var config_file="$v2" | grep -E 'Apply complete|Error'
"$py" "$here/verify.py" "$v2" --absent virtual:/Common/tftest-vs-l4 pool:/Common/tftest-l4 node:/Common/198.18.0.11
no_drift "$v2"

echo "--- step 3: destroy"
trap - ERR
tf destroy -auto-approve -var config_file="$v2" | grep -E 'Destroy complete|Error'
"$py" "$here/verify.py" "$here/fixtures/empty.yml" --absent "${all_absent[@]}"
rm -f "$here/live/drift.log"
echo "LIVE TEST PASSED"

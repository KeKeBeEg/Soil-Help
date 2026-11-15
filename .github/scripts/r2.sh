# Helpers shared by the field-run and index-rebuild workflows.
# Use in a step with:  source .github/scripts/r2.sh
# Needs R2_ENDPOINT and BUCKET_RESULTS; set_status also needs FIELD_KEY, FIELD_ID and RANGE.

# r2_exists <key>: true if that exact object exists in the results bucket.
r2_exists() {
  aws s3api head-object --bucket "$BUCKET_RESULTS" --key "$1" \
    --endpoint-url "$R2_ENDPOINT" > /dev/null 2>&1
}

# r2_put_json <key> < file: upload JSON that must never be cached (status, entries, index).
r2_put_json() {
  aws s3 cp - "s3://$BUCKET_RESULTS/$1" --content-type application/json \
    --cache-control no-store --endpoint-url "$R2_ENDPOINT" --only-show-errors
}

# set_status <state> <stage> [message]
# state: running | done | failed. The site writes "queued" before the run starts.
# One file per date range (status/<start>_<end>.json), which is what the site polls.
set_status() {
  [ -n "${FIELD_KEY:-}" ] && [ -n "${RANGE:-}" ] || return 0
  jq -n --arg state "$1" --arg stage "$2" --arg message "${3:-}" \
        --arg field_id "${FIELD_ID:-}" --arg range "$RANGE" --arg run_id "$GITHUB_RUN_ID" \
        --arg run_url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \
        --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{state: $state, stage: $stage, message: $message, field_id: $field_id, range: $range,
          run_id: $run_id, run_url: $run_url, updated_at: $updated_at}' \
    | r2_put_json "$FIELD_KEY/status/$RANGE.json"
}

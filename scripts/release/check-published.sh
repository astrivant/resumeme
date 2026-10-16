#!/usr/bin/env bash
# Audit the public release independently of whether its publisher ran.
set -euo pipefail
: "${RELEASE_TAG:?Set the immutable release tag}"
: "${SOURCE_SHA:?Set the immutable source revision}"
: "${GITHUB_REPOSITORY:?Set the publishing repository}"
expected_dir="${1:-.cache/expected-release}"
published_dir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/resumeme-published.XXXXXX")"

# Remove only this check's downloaded public artifacts on success, failure, or interruption.
# () -> ret::exit_code (original command status)
cleanup() {
    local status=$?
    rm -rf -- "$published_dir"
    exit "$status"
}
trap cleanup EXIT

bash scripts/tooling/retry.sh gh release view "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY" --json isDraft,tagName >"$published_dir/release.json"
jq -e --arg tag "$RELEASE_TAG" '.isDraft == false and .tagName == $tag' "$published_dir/release.json" >/dev/null
artifacts=(resume.pdf resume.pdf.sig resume.pdf.sigstore.json cosign.pub key-fingerprint.txt source.json SHA256SUMS SHA256SUMS.sigstore.json)
# Request the complete attachment set so a partial public release cannot pass.
patterns=()
for artifact in "${artifacts[@]}"; do
    patterns+=(--pattern "$artifact")
done
bash scripts/tooling/retry.sh gh release download "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY" --dir "$published_dir" "${patterns[@]}"

# Compare with this run's signing output rather than trusting a key supplied only by the public release.
for artifact in "${artifacts[@]}"; do
    test -s "$published_dir/$artifact"
done

# Signing retries may produce different bundles; compare stable inputs and verify downloaded signatures.
for artifact in resume.pdf cosign.pub key-fingerprint.txt source.json; do
    cmp "$expected_dir/$artifact" "$published_dir/$artifact"
done
jq -e --arg source "$SOURCE_SHA" '.source_commit == $source' "$published_dir/source.json" >/dev/null
bash scripts/release/verify.sh "$published_dir"

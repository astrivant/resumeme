#!/usr/bin/env bash
# Reject skipped, failed, or cancelled required publishers after tag verification.
set -euo pipefail
jq -e --arg tag "$RELEASE_TAG" '
  ([.source, .verified, .["release-stage"], .["container-stage"],
    .["tag-publish-stage"], .["tag-pages-stage"]] | all(.result == "success"))
  and (if $tag | startswith("v") then .["pypi-stage"].result == "success" else true end)
' <<<"$RESULTS_JSON"

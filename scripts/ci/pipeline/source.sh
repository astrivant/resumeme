#!/usr/bin/env bash
# Bind pipeline consumers to one source; collect only from authorized main events.
set -euo pipefail
refresh="${REFRESH_PROFILE:-false}"

# Main source pushes collect once; generated publication commits cannot recursively collect.
if [ "$GITHUB_EVENT_NAME" = push ] && [ "$GITHUB_REF" = refs/heads/main ]; then
    refresh=true
    author="$(git show -s --format=%an HEAD)"
    marker="$(git show -s --format='%(trailers:key=Resumeme-Publication,valueonly)' HEAD)"
    if [ "$author" = 'github-actions[bot]' ] && [ "$marker" = true ]; then
        refresh=false
    fi
elif [ "$refresh" = true ]; then
    if [ "$GITHUB_REF" != refs/heads/main ] || [[ ! "$GITHUB_EVENT_NAME" =~ ^(schedule|workflow_dispatch)$ ]]; then
        echo 'Profile refresh requires a main push, or a scheduled or requested manual run on main. Tags reuse accepted data.' >&2
        exit 1
    fi
fi

echo "sha=$(git rev-parse HEAD)" >>"$GITHUB_OUTPUT"
echo "refresh=$refresh" >>"$GITHUB_OUTPUT"

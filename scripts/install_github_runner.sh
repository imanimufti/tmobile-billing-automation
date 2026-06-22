#!/bin/bash
# Install a GitHub Actions self-hosted runner on THIS Mac so the billing pipeline
# can be triggered remotely (phone / web / API) via the "Run T-Mobile Pipeline"
# workflow. The runner executes the pipeline in your real install dir, reusing the
# Keychain, credentials.json, and seeded browser/WhatsApp sessions already there.
#
# Why self-hosted: the pipeline is macOS-local and secret-bound, so GitHub-hosted
# (cloud) runners can't run it. This puts the "Run workflow" button on your phone
# while the work still happens on your Mac.
#
# Usage:
#   ./scripts/install_github_runner.sh <registration-token>
#
# Get <registration-token> from:
#   GitHub repo -> Settings -> Actions -> Runners -> New self-hosted runner -> macOS
#   (copy the token shown in the ./config.sh line — it expires after ~1 hour).
#
# It installs the runner as a per-user LaunchAgent (svc.sh), so it runs inside your
# logged-in GUI session and inherits Full Disk Access / Accessibility — the same
# permissions the pipeline's launchd agent needs.
set -euo pipefail

REPO_URL="https://github.com/imanimufti/tmobile-billing-automation"
LABELS="tmobile"
RUNNER_DIR="$HOME/actions-runner"

TOKEN="${1:-}"
if [ -z "$TOKEN" ]; then
    echo "Usage: $0 <registration-token>" >&2
    echo "Get one at: $REPO_URL/settings/actions/runners/new?arch=$([ "$(uname -m)" = arm64 ] && echo arm64 || echo x64)&os=osx" >&2
    exit 1
fi

# Apple Silicon vs Intel.
case "$(uname -m)" in
    arm64)  ARCH="osx-arm64" ;;
    x86_64) ARCH="osx-x64" ;;
    *) echo "Unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

# Latest runner release tag (e.g. v2.319.1 -> 2.319.1).
echo "Resolving latest runner version..."
VERSION="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
    | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -n1)"
if [ -z "$VERSION" ]; then
    echo "Could not resolve latest runner version (rate-limited?). Set VERSION manually." >&2
    exit 1
fi
TARBALL="actions-runner-${ARCH}-${VERSION}.tar.gz"
URL="https://github.com/actions/runner/releases/download/v${VERSION}/${TARBALL}"

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [ ! -f "config.sh" ]; then
    echo "Downloading $TARBALL ..."
    curl -fsSL -o "$TARBALL" "$URL"
    tar xzf "$TARBALL"
    rm -f "$TARBALL"
fi

# Reconfigure cleanly if a previous registration exists.
if [ -f ".runner" ]; then
    echo "Removing previous runner registration..."
    ./svc.sh stop  2>/dev/null || true
    ./svc.sh uninstall 2>/dev/null || true
    ./config.sh remove --token "$TOKEN" 2>/dev/null || true
fi

echo "Configuring runner for $REPO_URL (labels: self-hosted, macOS, $LABELS)..."
./config.sh \
    --url "$REPO_URL" \
    --token "$TOKEN" \
    --labels "$LABELS" \
    --name "$(hostname -s)-tmobile" \
    --unattended \
    --replace

# Install as a per-user LaunchAgent so it runs in your GUI session (GUI session =
# Keychain + Accessibility + Full Disk Access available to the pipeline).
./svc.sh install
./svc.sh start

echo
echo "Runner installed and started."
echo "  Repo:   $REPO_URL"
echo "  Labels: self-hosted, macOS, $LABELS"
echo "  Status: $RUNNER_DIR/svc.sh status"
echo
echo "Trigger it from anywhere:"
echo "  - GitHub mobile app: Actions -> Run T-Mobile Pipeline -> Run workflow"
echo "  - CLI:               gh workflow run run-pipeline.yml"
echo
echo "If your install dir is NOT the default, set the repo variable TMOBILE_PROJECT_DIR"
echo "($REPO_URL/settings/variables/actions) to its path."

# Shared settings and helpers for the deploy/aws scripts. Sourced, not run.
#
# Everything the scripts create is tagged Project=$PROJECT_TAG, and what
# they need to remember (instance ids, addresses, the SSH key, the webhook
# signing secret) lives in deploy/aws/.state/, which is gitignored.

set -euo pipefail

# Git Bash on Windows rewrites arguments that look like paths (/aws/...,
# /dev/sda1). These are AWS names and remote paths, so leave them alone.
export MSYS_NO_PATHCONV=1
export AWS_PAGER=""
export AWS_REGION="${AWS_REGION:-ap-south-1}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
STATE_DIR="$HERE/.state"
STATE="$STATE_DIR/state.env"

PROJECT_TAG="${RELAY_DEPLOY_TAG:-relay-demo}"
# Relay runs Kafka, Postgres, Redis and five services: 8 GB.
RELAY_INSTANCE_TYPE="${RELAY_INSTANCE_TYPE:-t3.large}"
# Two small stores, but the image build needs more than a micro's 1 GB.
STORES_INSTANCE_TYPE="${STORES_INSTANCE_TYPE:-t3.small}"
KEY_NAME="$PROJECT_TAG-key"
KEY_FILE="$STATE_DIR/$KEY_NAME.pem"
SSH_USER=ubuntu

mkdir -p "$STATE_DIR"
if [ -f "$STATE" ]; then
  # shellcheck disable=SC1090
  . "$STATE"
fi

die() {
  echo "error: $*" >&2
  exit 1
}

# aws.exe on Windows ends lines with \r, which breaks comparisons.
awsq() {
  aws "$@" | tr -d '\r'
}

# Remember a value across scripts: save NAME VALUE
save() {
  touch "$STATE"
  grep -v "^$1=" "$STATE" > "$STATE.tmp" || true
  echo "$1=$2" >> "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
  printf -v "$1" '%s' "$2"
}

# A path aws.exe can read (file://...), on Windows or not.
native_path() {
  if command -v cygpath > /dev/null 2>&1; then
    cygpath -w "$1"
  else
    echo "$1"
  fi
}

ssh_to() {
  local host=$1
  shift
  ssh -i "$KEY_FILE" \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="$STATE_DIR/known_hosts" \
    -o ConnectTimeout=10 \
    -o ServerAliveInterval=30 \
    "$SSH_USER@$host" "$@"
}

scp_to() {
  local host=$1 source=$2 target=$3
  scp -i "$KEY_FILE" \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="$STATE_DIR/known_hosts" \
    "$source" "$SSH_USER@$host:$target"
}

# Wait until the instance accepts SSH and cloud-init (Docker install) is done.
wait_for_ssh() {
  local host=$1 tries=0
  until ssh_to "$host" 'cloud-init status --wait > /dev/null 2>&1; docker --version' > /dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -lt 60 ] || die "$host did not become reachable over SSH"
    sleep 5
  done
}

# Wait until a URL answers 2xx: wait_for_url URL LABEL
wait_for_url() {
  local url=$1 label=$2 tries=0
  until curl -sf -o /dev/null --max-time 5 "$url"; do
    tries=$((tries + 1))
    [ "$tries" -lt 90 ] || die "$label did not come up at $url"
    sleep 5
  done
  echo "  $label is up: $url"
}

# Start the containers on each instance (after deploy or a stop/start).
compose_up() {
  local build=${1:-}
  ssh_to "$RELAY_IP" "cd ~/relay && docker compose up -d $build"
  ssh_to "$STORES_IP" "cd ~/relay && docker compose --profile sdk-demo up -d $build store-acme store-bluebird"
}

require_provisioned() {
  [ -n "${RELAY_IP:-}" ] && [ -n "${STORES_IP:-}" ] || die "nothing provisioned yet; run deploy/aws/provision.sh"
}

print_urls() {
  cat <<EOF

  Dashboard        http://$RELAY_IP:8090
  Relay API        http://$RELAY_IP:8000/docs
  Acme inbox       http://$STORES_IP:8071
  Bluebird inbox   http://$STORES_IP:8072

  Onboard the merchants from this machine (PowerShell):
    deploy\\aws\\payflow.ps1 onboard acme http://$STORES_IP:8071/webhook
    deploy\\aws\\payflow.ps1 onboard bluebird http://$STORES_IP:8072/webhook
    deploy\\aws\\payflow.ps1 pay acme 499
EOF
}

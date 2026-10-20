#!/usr/bin/env bash
# Ship the committed code (git HEAD) to both instances and start it.
#
#   bash deploy/aws/deploy.sh
#
# Re-run after committing changes. Data (Postgres, Kafka, Redis, the store
# inboxes) is in Docker volumes and survives a redeploy; deploy/aws/reset.sh
# wipes it.

. "$(dirname "$0")/common.sh"
require_provisioned

if [ -z "${SIGNING_SECRET:-}" ]; then
  # Relay signs every webhook with it; the stores verify with it.
  save SIGNING_SECRET "$(openssl rand -hex 24)"
fi

COMMIT=$(git -C "$REPO" rev-parse --short HEAD)
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
  echo "warning: uncommitted changes are not deployed (deploying $COMMIT)"
fi

ARCHIVE="$STATE_DIR/relay.tar.gz"
git -C "$REPO" archive --format=tar.gz -o "$ARCHIVE" HEAD

push_code() {
  local host=$1
  echo "Copying $COMMIT to $host..."
  scp_to "$host" "$ARCHIVE" relay.tar.gz
  ssh_to "$host" 'rm -rf ~/relay.new && mkdir ~/relay.new && tar xzf relay.tar.gz -C ~/relay.new \
    && rm -rf ~/relay && mv ~/relay.new ~/relay && rm relay.tar.gz'
}

push_code "$RELAY_IP"
push_code "$STORES_IP"

# compose reads .env from the project directory.
ssh_to "$RELAY_IP" 'cat > ~/relay/.env' <<EOF
RELAY_PUBLIC_BIND=0.0.0.0
RELAY_CORS_ORIGINS=http://$RELAY_IP:8090
RELAY_WEBHOOK_SIGNING_SECRET=$SIGNING_SECRET
EOF
ssh_to "$STORES_IP" 'cat > ~/relay/.env' <<EOF
RELAY_PUBLIC_BIND=0.0.0.0
RELAY_WEBHOOK_SIGNING_SECRET=$SIGNING_SECRET
EOF

echo "Building and starting (the first build takes several minutes)..."
compose_up --build

echo "Waiting for health checks..."
wait_for_url "http://$RELAY_IP:8090/api/chart/endpoints" "Dashboard"
wait_for_url "http://$RELAY_IP:8000/docs" "Relay API"
wait_for_url "http://$STORES_IP:8071/healthz" "Acme Store"
wait_for_url "http://$STORES_IP:8072/healthz" "Bluebird Books"
save DEPLOYED_COMMIT "$COMMIT"

print_urls

#!/usr/bin/env bash
# Start the stopped instances and their containers (before working or a demo).
#
#   bash deploy/aws/start.sh

. "$(dirname "$0")/common.sh"
require_provisioned

aws ec2 start-instances --instance-ids "$RELAY_INSTANCE" "$STORES_INSTANCE" > /dev/null
echo "Waiting for the instances to run..."
aws ec2 wait instance-running --instance-ids "$RELAY_INSTANCE" "$STORES_INSTANCE"
wait_for_ssh "$RELAY_IP"
wait_for_ssh "$STORES_IP"

compose_up
wait_for_url "http://$RELAY_IP:8090/api/chart/endpoints" "Dashboard"
wait_for_url "http://$STORES_IP:8071/healthz" "Acme Store"
wait_for_url "http://$STORES_IP:8072/healthz" "Bluebird Books"

print_urls

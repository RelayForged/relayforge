#!/usr/bin/env bash
# Stop both instances. Compute billing stops; disks and the two Elastic IPs
# are still billed (cents a day). Data and registered merchants are kept.
#
#   bash deploy/aws/stop.sh

. "$(dirname "$0")/common.sh"
require_provisioned

aws ec2 stop-instances --instance-ids "$RELAY_INSTANCE" "$STORES_INSTANCE" > /dev/null
echo "Stopping..."
aws ec2 wait instance-stopped --instance-ids "$RELAY_INSTANCE" "$STORES_INSTANCE"
echo "Stopped. Start again with: bash deploy/aws/start.sh"

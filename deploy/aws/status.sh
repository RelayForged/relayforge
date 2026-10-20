#!/usr/bin/env bash
# Show both instances, the deployed commit and the URLs.
#
#   bash deploy/aws/status.sh

. "$(dirname "$0")/common.sh"
require_provisioned

awsq ec2 describe-instances --instance-ids "$RELAY_INSTANCE" "$STORES_INSTANCE" \
  --query 'Reservations[].Instances[].[Tags[?Key==`Role`]|[0].Value, InstanceId, InstanceType, State.Name, PublicIpAddress]' \
  --output table
echo "Deployed commit: ${DEPLOYED_COMMIT:-none}"
print_urls

#!/usr/bin/env bash
# Delete everything provision.sh created: instances (and their disks),
# Elastic IPs, security groups, the key pair and the local state.
#
#   bash deploy/aws/teardown.sh

. "$(dirname "$0")/common.sh"

echo "This permanently deletes every $PROJECT_TAG resource in $AWS_REGION, including all data."
read -r -p "Type DELETE to continue: " answer
[ "$answer" = "DELETE" ] || die "aborted"

INSTANCES=$(awsq ec2 describe-instances \
  --filters Name=tag:Project,Values="$PROJECT_TAG" \
  Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -n "$INSTANCES" ]; then
  # shellcheck disable=SC2086
  aws ec2 terminate-instances --instance-ids $INSTANCES > /dev/null
  echo "Terminating $INSTANCES..."
  # shellcheck disable=SC2086
  aws ec2 wait instance-terminated --instance-ids $INSTANCES
fi

for allocation in $(awsq ec2 describe-addresses --filters Name=tag:Project,Values="$PROJECT_TAG" \
  --query 'Addresses[].AllocationId' --output text); do
  aws ec2 release-address --allocation-id "$allocation"
  echo "Released Elastic IP $allocation"
done

for sg in $(awsq ec2 describe-security-groups --filters Name=tag:Project,Values="$PROJECT_TAG" \
  --query 'SecurityGroups[].GroupId' --output text); do
  aws ec2 delete-security-group --group-id "$sg"
  echo "Deleted security group $sg"
done

if aws ec2 describe-key-pairs --key-names "$KEY_NAME" > /dev/null 2>&1; then
  aws ec2 delete-key-pair --key-name "$KEY_NAME"
  echo "Deleted key pair $KEY_NAME"
fi

rm -rf "$STATE_DIR"
echo "Done. Nothing from $PROJECT_TAG is left running or billed."

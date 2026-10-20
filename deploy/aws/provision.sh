#!/usr/bin/env bash
# Create the two EC2 instances: Relay, and the Demo 2 merchant stores.
#
#   bash deploy/aws/provision.sh
#
# Safe to re-run: it reuses what already exists (by tag), and re-running
# from a new network adds that network's address to the SSH rules.
# Set BUDGET_EMAIL=you@example.com to also create a $20 monthly budget alarm.

. "$(dirname "$0")/common.sh"

aws sts get-caller-identity > /dev/null || die "AWS credentials are not working; run: aws configure"
ACCOUNT_ID=$(awsq sts get-caller-identity --query Account --output text)
MY_IP=$(curl -sf https://checkip.amazonaws.com | tr -d '\r\n')
echo "Account $ACCOUNT_ID, region $AWS_REGION, SSH allowed from $MY_IP"

VPC=$(awsq ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
[ "$VPC" != "None" ] || die "this region has no default VPC"
AMI=$(awsq ssm get-parameter \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)

TAGS="{Key=Project,Value=$PROJECT_TAG}"

# --- SSH key -----------------------------------------------------------------
if [ ! -f "$KEY_FILE" ]; then
  if aws ec2 describe-key-pairs --key-names "$KEY_NAME" > /dev/null 2>&1; then
    die "key pair $KEY_NAME exists in AWS but $KEY_FILE is missing; run teardown.sh first"
  fi
  awsq ec2 create-key-pair --key-name "$KEY_NAME" \
    --tag-specifications "ResourceType=key-pair,Tags=[$TAGS]" \
    --query KeyMaterial --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "Created key pair $KEY_NAME"
fi

# --- Security groups -----------------------------------------------------------
ensure_sg() {
  local name=$1 description=$2 id
  id=$(awsq ec2 describe-security-groups \
    --filters Name=group-name,Values="$name" Name=vpc-id,Values="$VPC" \
    --query 'SecurityGroups[0].GroupId' --output text)
  if [ "$id" = "None" ]; then
    id=$(awsq ec2 create-security-group --group-name "$name" --description "$description" \
      --vpc-id "$VPC" --tag-specifications "ResourceType=security-group,Tags=[$TAGS]" \
      --query GroupId --output text)
  fi
  echo "$id"
}

# allow SG PORT[-PORT] CIDR -- an existing identical rule is fine.
allow() {
  local out
  if ! out=$(aws ec2 authorize-security-group-ingress --group-id "$1" \
    --protocol tcp --port "$2" --cidr "$3" 2>&1); then
    case "$out" in
      *InvalidPermission.Duplicate*) ;;
      *) die "$out" ;;
    esac
  fi
}

RELAY_SG=$(ensure_sg "$PROJECT_TAG-relay" "Relay: API, dashboard, simulator")
STORES_SG=$(ensure_sg "$PROJECT_TAG-stores" "Relay Demo 2 merchant stores")
save RELAY_SG "$RELAY_SG"
save STORES_SG "$STORES_SG"

# No authentication yet: the dashboard, API and simulator are open to anyone
# with the address. Authentication is the next step (deploy/aws/README.md).
for port in 8000 8088 8090; do
  allow "$RELAY_SG" "$port" 0.0.0.0/0
done
# Inbox pages for the audience; webhooks are HMAC-verified by the store.
allow "$STORES_SG" 8071-8072 0.0.0.0/0
for sg in "$RELAY_SG" "$STORES_SG"; do
  allow "$sg" 22 "$MY_IP/32"
done

# --- Instances -----------------------------------------------------------------
launch() {
  local role=$1 type=$2 sg=$3 disk=$4 id
  id=$(awsq ec2 describe-instances \
    --filters Name=tag:Project,Values="$PROJECT_TAG" Name=tag:Role,Values="$role" \
    Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)
  if [ "$id" = "None" ]; then
    id=$(awsq ec2 run-instances --image-id "$AMI" --instance-type "$type" \
      --key-name "$KEY_NAME" --security-group-ids "$sg" \
      --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$disk,VolumeType=gp3}" \
      --user-data "file://$(native_path "$HERE/user-data.sh")" \
      --tag-specifications "ResourceType=instance,Tags=[$TAGS,{Key=Role,Value=$role},{Key=Name,Value=$PROJECT_TAG-$role}]" \
      --query 'Instances[0].InstanceId' --output text)
    echo "Launched $role instance $id ($type)" >&2
  fi
  echo "$id"
}

RELAY_INSTANCE=$(launch relay "$RELAY_INSTANCE_TYPE" "$RELAY_SG" 30)
STORES_INSTANCE=$(launch stores "$STORES_INSTANCE_TYPE" "$STORES_SG" 16)
save RELAY_INSTANCE "$RELAY_INSTANCE"
save STORES_INSTANCE "$STORES_INSTANCE"

aws ec2 start-instances --instance-ids "$RELAY_INSTANCE" "$STORES_INSTANCE" > /dev/null
echo "Waiting for the instances to run..."
aws ec2 wait instance-running --instance-ids "$RELAY_INSTANCE" "$STORES_INSTANCE"

# --- Fixed addresses -----------------------------------------------------------
# Elastic IPs keep each address across stop/start, so registered webhook
# URLs and the dashboard's CORS origin stay valid.
ensure_eip() {
  local role=$1 instance=$2 allocation
  allocation=$(awsq ec2 describe-addresses \
    --filters Name=tag:Project,Values="$PROJECT_TAG" Name=tag:Role,Values="$role" \
    --query 'Addresses[0].AllocationId' --output text)
  if [ "$allocation" = "None" ]; then
    allocation=$(awsq ec2 allocate-address --domain vpc \
      --tag-specifications "ResourceType=elastic-ip,Tags=[$TAGS,{Key=Role,Value=$role}]" \
      --query AllocationId --output text)
  fi
  aws ec2 associate-address --allocation-id "$allocation" --instance-id "$instance" > /dev/null
  awsq ec2 describe-addresses --allocation-ids "$allocation" --query 'Addresses[0].PublicIp' --output text
}

save RELAY_IP "$(ensure_eip relay "$RELAY_INSTANCE")"
save STORES_IP "$(ensure_eip stores "$STORES_INSTANCE")"

# --- Budget alarm (optional) ---------------------------------------------------
if [ -n "${BUDGET_EMAIL:-}" ]; then
  if ! aws budgets describe-budget --account-id "$ACCOUNT_ID" --budget-name "$PROJECT_TAG" > /dev/null 2>&1; then
    aws budgets create-budget --account-id "$ACCOUNT_ID" \
      --budget "BudgetName=$PROJECT_TAG,BudgetLimit={Amount=20,Unit=USD},TimeUnit=MONTHLY,BudgetType=COST" \
      --notifications-with-subscribers "Notification={NotificationType=ACTUAL,ComparisonOperator=GREATER_THAN,Threshold=80,ThresholdType=PERCENTAGE},Subscribers=[{SubscriptionType=EMAIL,Address=$BUDGET_EMAIL}]"
    echo "Budget alarm: email $BUDGET_EMAIL past \$16 of \$20 a month"
  fi
fi

echo "Waiting for SSH and Docker on both instances (first boot takes a few minutes)..."
wait_for_ssh "$RELAY_IP"
wait_for_ssh "$STORES_IP"

echo
echo "Provisioned. Relay $RELAY_IP ($RELAY_INSTANCE), stores $STORES_IP ($STORES_INSTANCE)"
echo "Next: bash deploy/aws/deploy.sh"

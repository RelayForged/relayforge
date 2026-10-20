#!/bin/bash
# First boot of each instance (EC2 user data): install Docker and add swap.
set -eux

curl -fsSL https://get.docker.com | sh
usermod -aG docker ubuntu

# Headroom for image builds (pip and the dashboard's node build).
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

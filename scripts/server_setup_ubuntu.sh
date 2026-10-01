#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04 / 24.04 server (x86_64 or ARM64, e.g. Oracle Cloud Always Free Ampere A1).
#   bash scripts/server_setup_ubuntu.sh
# Installs Docker Engine + Compose, opens ports 80/443 in the host firewall, adds swap. Safe to re-run.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
echo "==> Updating packages"
echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | sudo debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | sudo debconf-set-selections
sudo apt-get update -y
sudo -E apt-get install -y ca-certificates curl git netfilter-persistent iptables-persistent || \
  sudo -E apt-get install -y ca-certificates curl git

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker Engine + Compose plugin"
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER" || true
sudo systemctl enable --now docker

echo "==> Opening ports 80 and 443 in the host firewall"
# Oracle Cloud Ubuntu images ship iptables rules that REJECT everything except SSH. Insert ACCEPT rules
# before the first REJECT rule (cloud-level security lists must ALSO allow 80/443; see DEPLOY.md).
reject_line=$(sudo iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    if [ -n "${reject_line:-}" ]; then
      sudo iptables -I INPUT "$reject_line" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    else
      sudo iptables -A INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    fi
  fi
done
sudo netfilter-persistent save 2>/dev/null || true
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
  sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
fi

if ! swapon --show | grep -q .; then
  echo "==> Adding a 4 GB swap file (helps the first image build)"
  sudo fallocate -l 4G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096
  sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

echo
echo "Done. Public IP: $(curl -fsS https://api.ipify.org 2>/dev/null || echo 'see your cloud console')"
echo "Log out and back in (so your user can run docker), then continue with DEPLOY.md step 5."

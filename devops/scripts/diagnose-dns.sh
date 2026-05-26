#!/bin/bash
# DNS diagnostics for SentinelCam network
# Run this on chandler-gate (bastion) to check DNS configuration

echo "========================================="
echo "SentinelCam DNS Diagnostics"
echo "========================================="
echo ""

echo "--- Bastion DNS Configuration ---"
echo "dnsmasq status:"
systemctl is-active dnsmasq 2>/dev/null || echo "dnsmasq: NOT RUNNING"
echo ""

echo "--- dnsmasq Configuration Files ---"
if [ -f /etc/dnsmasq.d/sentinelcam.conf ]; then
    echo "Content of /etc/dnsmasq.d/sentinelcam.conf:"
    cat /etc/dnsmasq.d/sentinelcam.conf
else
    echo "⚠️  /etc/dnsmasq.d/sentinelcam.conf NOT FOUND"
fi
echo ""

echo "--- Test DNS Resolution (from bastion) ---"
for host in data1 alpha5 sentinel east lab1 wall1; do
    echo -n "$host: "
    dig @localhost $host.local +short 2>/dev/null || echo "FAILED"
done
echo ""

echo "--- Active DHCP Leases ---"
if [ -f /var/lib/misc/dnsmasq.leases ]; then
    echo "Current DHCP leases:"
    cat /var/lib/misc/dnsmasq.leases
elif [ -f /var/lib/dnsmasq/dnsmasq.leases ]; then
    echo "Current DHCP leases:"
    cat /var/lib/dnsmasq/dnsmasq.leases
else
    echo "No DHCP lease file found"
fi
echo ""

echo "--- Bastion's Own DNS Settings ---"
echo "Content of /etc/resolv.conf:"
cat /etc/resolv.conf
echo ""

echo "--- Test Ping to Known Nodes ---"
for host in data1 alpha5 sentinel; do
    echo -n "Ping $host: "
    ping -c 1 -W 1 $host >/dev/null 2>&1 && echo "✅ OK" || echo "❌ FAILED"
done
echo ""

echo "--- Check dnsmasq Logs for Errors ---"
echo "Recent dnsmasq log entries:"
journalctl -u dnsmasq -n 20 --no-pager
echo ""

echo "========================================="
echo "Quick Fixes:"
echo "========================================="
echo "If DNS resolution fails:"
echo "  1. Restart dnsmasq: sudo systemctl restart dnsmasq"
echo "  2. Check dnsmasq config: sudo dnsmasq --test"
echo "  3. Re-run bastion playbook: ansible-playbook playbooks/deploy-bastion.yaml --tags dns"
echo ""
echo "If nodes can't resolve bastion:"
echo "  1. Check node's /etc/resolv.conf has: nameserver 192.168.10.254"
echo "  2. Run: ansible modern_nodes -m shell -a 'cat /etc/resolv.conf'"

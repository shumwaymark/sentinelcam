#!/bin/bash
# Quick network diagnostics for SentinelCam nodes
# Run this on any node to see what network managers are fighting

echo "========================================="
echo "SentinelCam Network Diagnostics"
echo "========================================="
echo ""

echo "--- Node Information ---"
echo "Hostname: $(hostname)"
echo "Current User: $(whoami)"
echo ""

echo "--- Network Manager Status ---"
echo -n "dhcpcd: "
systemctl is-active dhcpcd 2>/dev/null || echo "not installed/inactive"
echo -n "  enabled: "
systemctl is-enabled dhcpcd 2>/dev/null || echo "N/A"
echo -n "  masked: "
systemctl is-masked dhcpcd 2>/dev/null && echo "yes" || echo "no"

echo -n "NetworkManager: "
systemctl is-active NetworkManager 2>/dev/null || echo "not installed/inactive"
echo -n "  enabled: "
systemctl is-enabled NetworkManager 2>/dev/null || echo "N/A"

echo -n "systemd-networkd: "
systemctl is-active systemd-networkd 2>/dev/null || echo "inactive"
echo -n "  enabled: "
systemctl is-enabled systemd-networkd 2>/dev/null || echo "N/A"
echo ""

echo "--- IP Addresses ---"
ip -4 -br addr show | grep -E "eth0|enp"
echo ""

echo "--- Default Route ---"
ip route show default
echo ""

echo "--- DNS Configuration ---"
echo "Resolver:"
cat /etc/resolv.conf | grep nameserver
echo ""

echo "--- Static Network Config Files ---"
ls -la /etc/systemd/network/*.network 2>/dev/null || echo "No systemd-networkd configs found"
echo ""

if [ -f /etc/systemd/network/10-eth0.network ]; then
    echo "--- Content of /etc/systemd/network/10-eth0.network ---"
    cat /etc/systemd/network/10-eth0.network
    echo ""
fi

echo "--- dhcpcd.conf (if exists) ---"
if [ -f /etc/dhcpcd.conf ]; then
    echo "dhcpcd.conf exists - THIS WILL OVERRIDE systemd-networkd!"
    grep -v "^#" /etc/dhcpcd.conf | grep -v "^$" | head -20
else
    echo "No dhcpcd.conf found (good)"
fi
echo ""

echo "========================================="
echo "Diagnosis:"
echo "========================================="
if systemctl is-active dhcpcd >/dev/null 2>&1; then
    echo "⚠️  PROBLEM: dhcpcd is running - this will override static IP"
    echo "   Fix: sudo systemctl stop dhcpcd && sudo systemctl mask dhcpcd"
fi

if systemctl is-active NetworkManager >/dev/null 2>&1; then
    echo "⚠️  PROBLEM: NetworkManager is running - may conflict with systemd-networkd"
    echo "   Fix: sudo systemctl stop NetworkManager && sudo systemctl mask NetworkManager"
fi

if ! systemctl is-active systemd-networkd >/dev/null 2>&1; then
    echo "⚠️  PROBLEM: systemd-networkd is not running"
    echo "   Fix: sudo systemctl enable systemd-networkd && sudo systemctl start systemd-networkd"
fi

if [ ! -f /etc/systemd/network/10-eth0.network ]; then
    echo "⚠️  PROBLEM: No static IP configuration file found"
    echo "   Fix: Run configure-static-network.yaml playbook"
fi

if systemctl is-masked dhcpcd >/dev/null 2>&1 && \
   systemctl is-active systemd-networkd >/dev/null 2>&1 && \
   [ -f /etc/systemd/network/10-eth0.network ]; then
    echo "✅ Configuration looks correct!"
    echo "   If IP is still wrong, try: sudo systemctl restart systemd-networkd"
    echo "   Or reboot: sudo reboot"
fi

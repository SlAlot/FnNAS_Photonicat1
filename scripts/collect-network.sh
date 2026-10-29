#!/bin/sh
# Read-only diagnostics: no module loading, network changes or reboot.

section() { printf '\n=== %s ===\n' "$1"; }
section 'Kernel and boot arguments'
uname -a
cat /proc/cmdline
section 'Board identity'
for item in model compatible; do
    printf '%s: ' "$item"
    if [ -r "/proc/device-tree/$item" ]; then
        tr '\000' '\n' < "/proc/device-tree/$item"
    fi
done
section 'Network interfaces and addresses'
ip -br link
ip -br addr
ip route
section 'Network sharing profiles'
nmcli -f NAME,TYPE,DEVICE connection show --active 2>/dev/null || true
nmcli -f GENERAL,IP4 device show pcatlan0 2>/dev/null || true
iptables -S FORWARD 2>/dev/null || true
iptables -t nat -S POSTROUTING 2>/dev/null || true
section 'Physical network device state'
for path in /sys/class/net/*; do
    [ -d "$path/device" ] || continue
    printf '\n%s\n' "${path##*/}"
    readlink -f "$path/device/driver"
    readlink -f "$path/device/of_node"
    for item in operstate carrier; do
        printf '%s: ' "$item"
        cat "$path/$item" 2>/dev/null || true
    done
    if command -v ethtool >/dev/null 2>&1; then
        ethtool -i "${path##*/}" 2>/dev/null || true
        ethtool "${path##*/}" 2>/dev/null || true
    fi
done
section 'Network and power modules'
lsmod | grep -Ei 'stmmac|dwmac|motorcomm|xpcs|phylink|pcat|photonicat' || true
section 'MDIO devices'
for path in /sys/bus/mdio_bus/devices/*; do
    [ -e "$path" ] || continue
    printf '\n%s\n' "${path##*/}"
    readlink -f "$path/driver"
    readlink -f "$path/of_node"
    for item in phy_id modalias; do
        [ ! -r "$path/$item" ] || cat "$path/$item"
    done
done
section 'Actual module identity'
for module in dwmac_rk stmmac motorcomm pcs_xpcs; do
    path=$(modinfo -n "$module" 2>/dev/null) || continue
    printf '%s: %s\n' "$module" "$path"
    [ ! -f "$path" ] || sha256sum "$path"
done
section 'Boot device tree selection'
if [ -r /boot/armbianEnv.txt ]; then
    grep -E '^(fdtfile|overlay_prefix|overlays|user_overlays)=' /boot/armbianEnv.txt || true
fi
section 'Kernel messages'
dmesg | grep -Ei 'ethernet|stmmac|dwmac|motorcomm|mdio|xpcs|phy |phy:|pcat|photonicat|defer|fail|error' | tail -n 180
section 'Service state'
systemctl --no-pager --full status systemd-networkd NetworkManager pcat-manager swrt-pcat-manager 2>&1 || true
section 'Network service messages'
journalctl -b --no-pager -n 80 -u systemd-networkd -u NetworkManager 2>&1 || true
section 'Listening web services'
ss -lnt | grep -E 'State|:5666 |:8000 |:8001 ' || true

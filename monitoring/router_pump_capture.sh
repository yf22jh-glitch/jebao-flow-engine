#!/usr/bin/env bash
# Read-only router/AP capture for the three specified pumps on every port.
# This does not redirect traffic or send commands to a device.
set -Eeuo pipefail
umask 077

if (( $# != 1 )); then
  echo "usage: router_pump_capture.sh any|phy0-ap0|phy1-ap0|br-lan.30" >&2
  exit 2
fi
case "$1" in
  any|phy0-ap0|phy1-ap0|br-lan.30) interface=$1 ;;
  *) echo "unsupported router interface" >&2; exit 2 ;;
esac

for program in docker findmnt jq ssh stat tcpdump; do
  command -v "$program" >/dev/null || {
    echo "missing capture dependency: $program" >&2
    exit 2
  }
done

capture_root=/srv/jebao-captures
[[ -d "$capture_root" && -w "$capture_root" ]] || {
  echo "large capture mount is unavailable" >&2
  exit 2
}
[[ $(findmnt -T "$capture_root" -no TARGET) == /srv ]] || {
  echo "large /srv capture volume is not mounted" >&2
  exit 2
}
capture_dir="$capture_root/jebao-router-allports-20260928-03"
mkdir -p -m 700 -- "$capture_dir"
[[ $(stat -c '%a' "$capture_dir") == 700 ]] || {
  echo "capture directory permissions are not private" >&2
  exit 2
}

# Use the dedicated target-only host collector's exact three-device bindings.
# IP and MAC are complementary: router/AP Ethernet can see the device MAC,
# while current routed flows are also covered by host IP.
ip_filter=$(
  docker inspect jebao-target-only-any-20260928 |
    jq -er '.[0].Config.Labels["local.jebao.target_ip_bpf"]'
)
mac_filter=$(
  docker inspect jebao-target-only-any-20260928 |
    jq -er '.[0].Config.Labels["local.jebao.target_mac_bpf"]'
)
if printf '%s\n' "$ip_filter" "$mac_filter" |
  grep -Eqv '^[A-Za-z0-9.:() ]+$'; then
  echo "capture filter contains unexpected characters" >&2
  exit 2
fi
printf '%s\n' "$ip_filter" |
  grep -Eq '^host ([0-9]{1,3}\.){3}[0-9]{1,3}( or host ([0-9]{1,3}\.){3}[0-9]{1,3}){2}$' || {
  echo "expected exactly three pump IP bindings" >&2
  exit 2
}
printf '%s\n' "$mac_filter" |
  grep -Eiq '^ether host ([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}( or ether host ([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}){2}$' || {
  echo "expected exactly three pump MAC bindings" >&2
  exit 2
}
if [[ "$interface" == any ]]; then
  # Linux cooked capture cannot compile `ether host`. The AP Ethernet captures
  # below keep MAC-only frames (including ARP); this router-wide view matches
  # the same three devices by IP on every interface and every port.
  filter=$ip_filter
else
  filter="($ip_filter) or ($mac_filter)"
fi

started_at=$(date -u +%Y%m%dT%H%M%S%N)
output="$capture_dir/router-${interface//./_}-$started_at.pcap"
[[ ! -e "$output" ]] || {
  echo "capture output already exists" >&2
  exit 2
}

echo "Starting read-only router capture on $interface; 50 MB x 80-file ring." >&2
ssh -n -o BatchMode=yes -o ConnectTimeout=8 \
  -o ServerAliveInterval=20 -o ServerAliveCountMax=3 router \
  "exec tcpdump -i '$interface' -n -s 0 -U -w - '$filter'" |
  tcpdump -n -r - -w "$output" -C 50 -W 80

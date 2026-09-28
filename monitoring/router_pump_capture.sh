#!/usr/bin/env bash
# Read-only router/AP packet capture streamed into the existing large capture mount.
# This does not redirect traffic or send commands to a device.
set -Eeuo pipefail
umask 077

if (( $# != 1 )); then
  echo "usage: router_pump_capture.sh phy0-ap0|br-lan.30" >&2
  exit 2
fi
case "$1" in
  phy0-ap0|br-lan.30) interface=$1 ;;
  *) echo "unsupported router interface" >&2; exit 2 ;;
esac

for program in docker jq ssh tcpdump; do
  command -v "$program" >/dev/null || {
    echo "missing capture dependency: $program" >&2
    exit 2
  }
done

capture_root=$(
  docker inspect lg-correlation-capture |
    jq -er '.[0].Mounts[] | select(.Destination == "/captures") | .Source'
)
[[ -d "$capture_root" && -w "$capture_root" ]] || {
  echo "large capture mount is unavailable" >&2
  exit 2
}
capture_dir="$capture_root/jebao-router-allports-20260928-01"
mkdir -p -m 700 -- "$capture_dir"
[[ $(stat -c '%a' "$capture_dir") == 700 ]] || {
  echo "capture directory permissions are not private" >&2
  exit 2
}

# Use the exact current pump bindings already resolved by the running all-port
# collectors.  IP and MAC are complementary: router/AP Ethernet can see the
# device MAC, while current routed flows are also covered by host IP.
ip_filter=$(
  docker inspect jebao-wavemakers-any-lg-20260928-01 |
    jq -er '.[0].Config.Cmd[-1]'
)
mac_filter=$(
  docker inspect jebao-wavemakers-allports-lg-20260928-01 |
    jq -er '.[0].Config.Cmd[-1]'
)
[[ "$ip_filter" == *"port 12416 or port 12414 or arp" ]] || {
  echo "current IP capture filter has unexpected shape" >&2
  exit 2
}
[[ "$mac_filter" == *"port 12416 or port 12414 or arp" ]] || {
  echo "current MAC capture filter has unexpected shape" >&2
  exit 2
}
if printf '%s\n' "$ip_filter" "$mac_filter" |
  grep -Eqv '^[A-Za-z0-9.:() ]+$'; then
  echo "capture filter contains unexpected characters" >&2
  exit 2
fi
filter="($ip_filter) or ($mac_filter)"

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

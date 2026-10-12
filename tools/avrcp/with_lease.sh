#!/bin/sh
# usage: with_lease.sh script.py  -- runs it only while holding the browse lease as "diag"
for i in $(seq 1 40); do
  mosquitto_pub -t jukebox/jb-source-phone/cmd -m '{"action": "lease_request", "holder": "diag", "ttl_s": 600}'
  sleep 1.5
  s=$(timeout 2 mosquitto_sub -C 1 -t jukebox/jb-source-phone/lease)
  case "$s" in *'"holder": "diag"'*) echo "lease granted"; break;; esac
  echo "lease not yet: $s" | cut -c1-110
  sleep 10
done
case "$s" in *'"holder": "diag"'*) ;; *) echo "NO LEASE -- not running"; exit 1;; esac
timeout 900 /opt/juk3-box/venv/bin/python -u "$@" 2>&1 | grep -v '^  File \|^\s*\^\|^$'
mosquitto_pub -t jukebox/jb-source-phone/cmd -m '{"action": "lease_release", "holder": "diag"}'
echo "lease released"

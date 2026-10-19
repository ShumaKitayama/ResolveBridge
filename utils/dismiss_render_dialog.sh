#!/bin/sh
# Close every stacked Fusion "Render completed!" popup (each blocks the scripting API until dismissed).
d=$(dirname "$0"); t=0
for i in $(seq 1 30); do n=$("$d/dismiss_once.sh"); [ "$n" = "1" ] || break; t=$((t+1)); sleep 0.3; done
echo $t

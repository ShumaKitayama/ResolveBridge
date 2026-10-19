#!/bin/sh
# Close Fusion's "Render completed!" popups (they block the scripting API until dismissed).
osascript <<'OSA' 2>/dev/null
tell application "System Events"
  tell process "Resolve"
    set n to 0
    repeat with w in windows
      try
        if (count of buttons of w) is 1 and name of button 1 of w is "OK" then
          click button 1 of w
          set n to n + 1
        end if
      end try
    end repeat
    return n
  end tell
end tell
OSA

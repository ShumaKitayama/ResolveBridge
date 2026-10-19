#!/usr/bin/env python3
"""Render chosen frames (or a range) of the FCS comp through a Saver via the bridge.

  render_frames.py 8 24 32 ...            -> renders/frames/frame_XXXX.png (FCS_PreviewSaver)
  render_frames.py --range 0 119          -> full range
  render_frames.py --saver FCS_AlphaSaver --range 0 119
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
import bridge_client as bc  # noqa: E402

args = sys.argv[1:]
saver = "FCS_PreviewSaver"
if "--saver" in args:
    i = args.index("--saver")
    saver = args[i + 1]
    del args[i:i + 2]
if "--range" in args:
    i = args.index("--range")
    spans = [(int(args[i + 1]), int(args[i + 2]))]
else:
    spans = [(int(f), int(f)) for f in args]

code = """
local c = S.comp or comp
local sv = c:FindTool(%r)
assert(sv, "saver missing")
sv:SetAttrs({ TOOLB_PassThrough = false })
local res = {}
for _, sp in ipairs({%s}) do
  local t0 = os.clock()
  local ok = c:Render({ Start = sp[1], End = sp[2], Wait = true, Tool = sv })
  res[#res + 1] = { sp[1], sp[2], ok, os.clock() - t0 }
end
sv:SetAttrs({ TOOLB_PassThrough = true })
return res
""" % (saver, ",".join("{%d,%d}" % s for s in spans))
t0 = time.time()
r = bc.run(code, 3600)
print(r, "wall %.1fs" % (time.time() - t0))

#!/usr/bin/env python3
"""Export every node whose name starts with PREFIX (default FCS_) as one Fusion .setting file.

  export_setting.py OUT.setting [--prefix FCS_]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
import bridge_client as bc  # noqa: E402

args = sys.argv[1:]
prefix = "FCS_"
if "--prefix" in args:
    i = args.index("--prefix"); prefix = args[i + 1]; del args[i:i + 2]
if not args:
    sys.exit("usage: export_setting.py OUT.setting [--prefix FCS_]")
out = os.path.expanduser(args[0])
code = ('local PREFIX = "%s"\n' % prefix) + r"""
local c = S.comp or comp
local list = {}
for _, t in pairs(c:GetToolList(false)) do
  local n = t:GetAttrs().TOOLS_Name or ""
  if n:sub(1, #PREFIX) == PREFIX then list[#list + 1] = t end
end
local st = c:CopySettings(list)
return { n = #list, text = bmd.writestring(st) }
"""
r = bc.run(code, 120)
if not r.get("ok"):
    print(r)
    sys.exit(1)
txt = r["result"]["text"]
with open(out, "w") as f:
    f.write("-- Nodes with prefix " + prefix + " exported by export_setting.py from the live comp.\n")
    f.write("-- Paste into a Fusion comp (Cmd+V after copying this text) or drag the file onto the Node editor.\n")
    f.write(txt)
print({"nodes": r["result"]["n"], "bytes": len(txt), "file": out})

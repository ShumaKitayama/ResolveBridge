#!/usr/bin/env python3
"""fusion-bridge: MCP server for DaVinci Resolve (works on the FREE edition 21.1) via claude_fusion_bridge.lua.

Every tool is turned into a small Lua chunk that runs inside Resolve's Fusion (through the file/log bridge).
All user-supplied values are embedded with bridge_client.lua_literal(), i.e. as escaped Lua literals,
never as code. Mutating tools are wrapped in one Fusion undo step (Cmd+Z in Resolve reverts them).

Typed tools ........ comp_info, add_tool, delete_tool, tool_info, connect_nodes, set_parameter,
                     set_expression, animate_parameter, add_text, list_fonts, add_mask,
                     render_tool_frame, resolve_info, open_page
Escape hatch ....... fusion_lua, fusion_lua_file  (arbitrary Lua: comp, fu, resolve, bmd, S)
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_client as bc  # noqa: E402
from mcp.server.fastmcp import FastMCP, Image  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

mcp = FastMCP("fusion-bridge")
L = bc.lua_literal
RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
EDIT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

PRELUDE = r"""
local c = comp or (S and S.comp) or fu:GetCurrentComp()
if not c then error("no Fusion composition: open a clip on the Fusion page") end
local function T(name)
  local t = c:FindTool(name)
  if not t then error("tool not found: " .. tostring(name)) end
  return t
end
local function IN(t, id)
  local i = t[id]
  if not i then error("input not found: " .. tostring(id) .. " on " .. t:GetAttrs().TOOLS_Name) end
  return i
end
local function val(v, d)
  d = d or 0
  local ty = type(v)
  if ty == "number" or ty == "string" or ty == "boolean" or ty == "nil" then return v end
  if ty == "table" and d < 3 then
    local o = {}
    for k, x in pairs(v) do
      if type(x) ~= "function" and k ~= "__flags" then o[tostring(k)] = val(x, d + 1) end
    end
    return o
  end
  return tostring(v)
end
-- Adding a node at the default position (-32768) makes Fusion auto-connect / auto-merge it to the
-- currently selected node (UI behaviour). Always pass explicit coordinates to avoid surprise links.
local function freepos()
  local n = 0
  for _ in pairs(c:GetToolList(false)) do n = n + 1 end
  return 2 * (n % 12), 20 + 2 * math.floor(n / 12)
end
local function ADD(id, x, y)
  if x == nil or y == nil then x, y = freepos() end
  local t = c:AddTool(id, x, y)
  if not t then error("unknown tool id: " .. tostring(id)) end
  return t
end
local function info(t)
  local a = t:GetAttrs()
  return { name = a.TOOLS_Name, type = a.TOOLS_RegID }
end
"""


def _run(body, timeout=120.0, undo=None):
    code = PRELUDE
    if undo:
        code += "c:StartUndo(%s)\nlocal __ok, __res = pcall(function()\n%s\nend)\nc:EndUndo(true)\n" \
                "if not __ok then error(__res, 0) end\nreturn __res\n" % (L(undo), body)
    else:
        code += body
    res = bc.run(code, timeout)
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "bridge error"))
    return res.get("result")


# ---------------------------------------------------------------- bridge / escape hatch
@mcp.tool(annotations=RO)
def bridge_status() -> dict:
    """Check that claude_fusion_bridge is running inside DaVinci Resolve (and whether auth is on)."""
    s = bc.session()
    if not s:
        return {"alive": False, "hint": "Fusion page > Workspace > Scripts > claude_fusion_bridge"}
    r = dict(s, **bc.run("return 'pong'", 10))
    if s.get("auth") == "off":
        r["warning"] = "bridge runs without auth; restart claude_fusion_bridge in Resolve to load runtime/token.lua"
    return r


@mcp.tool(annotations=DESTRUCTIVE)
def fusion_lua(code: str, timeout: float = 120.0) -> dict:
    """Escape hatch: run arbitrary Lua inside Resolve's Fusion. Globals: comp (current comp), fu, resolve
    (full Resolve API), bmd, S (table persisting across calls). `return` a value to get it back as JSON;
    print() output is captured. Prefer the typed tools when they fit."""
    return bc.run(code, timeout)


@mcp.tool(annotations=DESTRUCTIVE)
def fusion_lua_file(path: str, timeout: float = 600.0) -> dict:
    """Escape hatch: run a .lua file inside Resolve's Fusion (same environment as fusion_lua)."""
    with open(os.path.expanduser(path)) as f:
        return bc.run(f.read(), timeout)


# ---------------------------------------------------------------- comp / tools
@mcp.tool(annotations=RO)
def comp_info(include_connections: bool = True) -> dict:
    """Current Fusion composition: name, frame range, current frame and every node (name, type, and which
    image/mask inputs are connected to which node)."""
    return _run(r"""
local a = c:GetAttrs()
local tools = {}
for _, t in pairs(c:GetToolList(false)) do
  local e = info(t)
  if %s then
    local con = {}
    for _, inp in pairs(t:GetInputList()) do
      local ia = inp:GetAttrs()
      if ia.INPS_DataType == "Image" or ia.INPS_DataType == "Mask" then
        local o = inp:GetConnectedOutput()
        if o then con[ia.INPS_ID] = o:GetTool():GetAttrs().TOOLS_Name .. "." .. o:GetAttrs().OUTS_ID end
      end
    end
    if next(con) then e.inputs = con end
  end
  tools[#tools + 1] = e
end
table.sort(tools, function(x, y) return x.name < y.name end)
return { name = a.COMPS_Name, render_start = a.COMPN_RenderStart, render_end = a.COMPN_RenderEnd,
         current_frame = c.CurrentTime, width = c:GetPrefs("Comp.FrameFormat.Width"),
         height = c:GetPrefs("Comp.FrameFormat.Height"), fps = c:GetPrefs("Comp.FrameFormat.Rate"),
         tool_count = #tools, tools = tools }
""" % ("true" if include_connections else "false"))


@mcp.tool(annotations=EDIT)
def add_tool(tool_id: str, name: str | None = None, x: int | None = None, y: int | None = None) -> dict:
    """Add a node by its Fusion registry ID (e.g. Background, Blur, Merge, TextPlus, Transform, ColorCorrector,
    EllipseMask, Custom (=Custom Tool), FastNoise, pEmitter, Saver). Optionally give it a name and a flow
    position (grid units)."""
    return _run("""
local t = ADD(%s, %s, %s)
if %s then t:SetAttrs({ TOOLS_Name = %s }) end
return info(t)
""" % (L(tool_id), L(x), L(y), "true" if name else "false", L(name)), undo="Add " + tool_id)


@mcp.tool(annotations=DESTRUCTIVE)
def delete_tool(name: str) -> dict:
    """Delete a node by name (one undo step)."""
    return _run("local t = T(%s); local e = info(t); t:Delete(); return { deleted = e }" % L(name),
                undo="Delete " + name)


@mcp.tool(annotations=RO)
def tool_info(name: str, filter: str | None = None, limit: int = 120) -> dict:
    """Inputs of a node with their current values: ID, display name, data type, value at the current frame,
    connection (for image/mask inputs), expression and whether it is animated. `filter` = case-insensitive
    substring on the ID or name."""
    return _run(r"""
local t = T(%s)
local f = %s
f = f and f:lower()
local out = {}
for _, inp in pairs(t:GetInputList()) do
  local a = inp:GetAttrs()
  local id = a.INPS_ID
  local nm = a.INPS_Name or ""
  if (not f or id:lower():find(f, 1, true) or nm:lower():find(f, 1, true)) and #out < %d then
    local e = { id = id, name = nm, type = a.INPS_DataType }
    local o = inp:GetConnectedOutput()
    if a.INPS_DataType == "Image" or a.INPS_DataType == "Mask" then
      e.connected = o and (o:GetTool():GetAttrs().TOOLS_Name .. "." .. o:GetAttrs().OUTS_ID) or nil
    else
      e.value = val(inp[c.CurrentTime])
      if o then e.animated_by = o:GetTool():GetAttrs().TOOLS_RegID end
      local ex = inp:GetExpression()
      if ex and ex ~= "" then e.expression = ex end
    end
    out[#out + 1] = e
  end
end
table.sort(out, function(x, y) return x.id < y.id end)
return { tool = info(t), inputs = out }
""" % (L(name), L(filter), int(limit)))


@mcp.tool(annotations=EDIT)
def connect_nodes(from_tool: str, to_tool: str, to_input: str = "Input", from_output: str = "Output") -> dict:
    """Connect from_tool's output to to_tool's input. Common inputs: Input, Background, Foreground (Merge),
    EffectMask, Image1/Image2 (Custom Tool). Masks: from_output="Mask"."""
    return _run("""
local src, dst = T(%s), T(%s)
local out = src[%s]
if not out then error("output not found: " .. %s) end
local inp = IN(dst, %s)
inp:ConnectTo(out)
local o = inp:GetConnectedOutput()
return { connected = o ~= nil, from = %s .. "." .. %s, to = %s .. "." .. %s }
""" % (L(from_tool), L(to_tool), L(from_output), L(from_output), L(to_input),
       L(from_tool), L(from_output), L(to_tool), L(to_input)), undo="Connect")


def _fusion_value(value):
    if isinstance(value, bool):
        return 1 if value else 0
    return value


@mcp.tool(annotations=EDIT)
def set_parameter(tool: str, input: str, value: float | int | str | bool | list[float],
                  frame: float | None = None) -> dict:
    """Set an input: number, text (e.g. StyledText, combo names), bool (checkboxes) or [x, y] point
    (e.g. Center). Coordinates are normalised (0..1, y up). With `frame`, sets the value at that frame
    (creates a keyframe if the input is animated). Use tool_info to discover input IDs."""
    v = _fusion_value(value)
    return _run("""
local t = T(%s)
local inp = IN(t, %s)
local fr = %s
if fr then inp[fr] = %s else t:SetInput(%s, %s) end
return { tool = %s, input = %s, value = val(inp[fr or c.CurrentTime]) }
""" % (L(tool), L(input), L(frame), L(v), L(input), L(v), L(tool), L(input)), undo="Set " + input)


@mcp.tool(annotations=EDIT)
def set_expression(tool: str, input: str, expression: str | None) -> dict:
    """Drive an input with a Fusion Simple Expression (Lua syntax, radians; `time` = current frame; other
    nodes by name, e.g. "Blur1.XBlurSize * 2"). Pass null/empty to remove the expression."""
    return _run("""
local t = T(%s)
local inp = IN(t, %s)
local ex = %s
if ex == nil or ex == "" then inp:SetExpression(nil) else inp:SetExpression(ex) end
return { tool = %s, input = %s, expression = inp:GetExpression(), value = val(inp[c.CurrentTime]) }
""" % (L(tool), L(input), L(expression), L(tool), L(input)), undo="Expression " + input)


@mcp.tool(annotations=EDIT)
def animate_parameter(tool: str, input: str, keyframes: list[list[float]]) -> dict:
    """Keyframe an input. keyframes = [[frame, value], ...] for numbers, or [[frame, x, y], ...] for point
    inputs such as Center (normalised 0..1). Replaces existing animation on that input."""
    kf = [[float(k[0])] + [float(x) for x in k[1:]] for k in keyframes]
    return _run("""
local t = T(%s)
local inp = IN(t, %s)
local kf = %s
local isPoint = inp:GetAttrs().INPS_DataType == "Point"
if isPoint then t[%s] = c:Path() else t[%s] = c:BezierSpline() end
inp = t[%s]
for _, k in ipairs(kf) do
  if isPoint then inp[k[1]] = { k[2], k[3] } else inp[k[1]] = k[2] end
end
local check = {}
for _, k in ipairs(kf) do check[#check + 1] = { k[1], val(inp[k[1]]) } end
return { tool = %s, input = %s, keyframes = check, modifier = isPoint and "Path" or "BezierSpline" }
""" % (L(tool), L(input), L(kf), L(input), L(input), L(input), L(tool), L(input)), undo="Animate " + input)


FONT_LUA = r"""
local function pickStyle(font, style)
  local list = fu.FontManager:GetFontList()
  local styles = list[font]
  if not styles then
    local near = {}
    local q = font:lower():sub(1, 4)
    for k in pairs(list) do if k:lower():find(q, 1, true) and #near < 8 then near[#near + 1] = k end end
    error("font not found: " .. font .. (#near > 0 and (" (similar: " .. table.concat(near, ", ") .. ")") or ""))
  end
  if style then
    if styles[style] then return style end
    local have = {}
    for k in pairs(styles) do have[#have + 1] = k end
    table.sort(have)
    error("style '" .. style .. "' not in " .. font .. ": " .. table.concat(have, ", "))
  end
  for _, s in ipairs({ "Regular", "Medium", "W4", "W3", "Book", "Normal", "Roman", "R" }) do
    if styles[s] then return s end
  end
  local have = {}
  for k in pairs(styles) do have[#have + 1] = k end
  table.sort(have)
  return have[1]
end
"""


@mcp.tool(annotations=RO)
def list_fonts(query: str = "", limit: int = 40) -> dict:
    """Fonts Fusion can use (Text+), with their style names. `query` = case-insensitive substring
    (e.g. "hiragino", "gothic", "noto"). Japanese text needs a Japanese font such as "Hiragino Sans" (W0-W9)."""
    return _run(r"""
local list = fu.FontManager:GetFontList()
local q = (%s):lower()
local out, total = {}, 0
local names = {}
for k in pairs(list) do names[#names + 1] = k end
table.sort(names)
for _, k in ipairs(names) do
  if q == "" or k:lower():find(q, 1, true) then
    total = total + 1
    if #out < %d then
      local st = {}
      for s in pairs(list[k]) do st[#st + 1] = s end
      table.sort(st)
      out[#out + 1] = { font = k, styles = st }
    end
  end
end
return { matches = total, fonts = out }
""" % (L(query), int(limit)))


@mcp.tool(annotations=EDIT)
def add_text(text: str, size: float = 0.08, color: list[float] | None = None,
             center: list[float] | None = None, name: str | None = None, font: str | None = None,
             style: str | None = None) -> dict:
    """Create a Text+ node with text, size (fraction of frame width), RGB colour (0..1), centre (normalised)
    and optional font/style in one step. If only `font` is given, a valid style of that font is chosen
    automatically (an invalid font/style pair makes Text+ fail to render). Use list_fonts to browse fonts."""
    col = color or [1.0, 1.0, 1.0]
    cen = center or [0.5, 0.5]
    return _run(FONT_LUA + """
local font, style = %s, %s
if font then style = pickStyle(font, style) end
local t = ADD("TextPlus")
if %s then t:SetAttrs({ TOOLS_Name = %s }) end
t.StyledText = %s
t.Size = %s
t.Red1 = %s; t.Green1 = %s; t.Blue1 = %s
t.Center = %s
if font then t.Font = font; t.Style = style end
local r = info(t)
r.font = t.Font[c.CurrentTime]; r.style = t.Style[c.CurrentTime]
return r
""" % (L(font), L(style), "true" if name else "false", L(name), L(text), L(size), L(col[0]), L(col[1]),
       L(col[2]), L(cen)), undo="Add Text")


@mcp.tool(annotations=EDIT)
def add_mask(shape: str, target: str | None = None, center: list[float] | None = None,
             width: float = 0.4, height: float = 0.4, soft_edge: float = 0.0, angle: float = 0.0,
             invert: bool = False, points: list[list[float]] | None = None, name: str | None = None) -> dict:
    """Create an ellipse / rectangle / polygon mask and (optionally) plug it into target's EffectMask.
    center/points are normalised image coordinates (0..1, y up); width/height are fractions of the frame
    width. For a polygon pass points=[[x, y], ...] (closed shape)."""
    ids = {"ellipse": "EllipseMask", "rectangle": "RectangleMask", "polygon": "PolylineMask"}
    if shape not in ids:
        raise ValueError("shape must be ellipse, rectangle or polygon")
    pts = [[float(p[0]) - 0.5, float(p[1]) - 0.5] for p in (points or [])]
    return _run("""
local m = ADD(%s)
if %s then m:SetAttrs({ TOOLS_Name = %s }) end
local cen = %s
if %s ~= "PolylineMask" then
  m.Center = cen; m.Width = %s; m.Height = %s; m.Angle = %s
end
m.SoftEdge = %s
m.Invert = %s
local pts = %s
if #pts > 0 then
  local P = {}
  for i, p in ipairs(pts) do P[i] = { X = p[1], Y = p[2], LX = 0, LY = 0, RX = 0, RY = 0, Linear = true } end
  m.Polyline = { Closed = true, Points = P }
end
local tgt = %s
if tgt then IN(T(tgt), "EffectMask"):ConnectTo(m.Mask) end
local r = info(m); r.connected_to = tgt
return r
""" % (L(ids[shape]), "true" if name else "false", L(name), L(center or [0.5, 0.5]), L(ids[shape]),
       L(width), L(height), L(angle), L(soft_edge), L(1 if invert else 0), L(pts), L(target)),
        undo="Add Mask")


@mcp.tool(annotations=EDIT)
def render_tool_frame(tool: str, frame: int | None = None, out_dir: str = "",
                      preview_width: int = 960) -> list:
    """Render one frame of any node's output to PNG (through a temporary Saver that is removed afterwards)
    and return the image so it can be inspected. Returns [metadata, image]."""
    out_dir = os.path.expanduser(out_dir) if out_dir else os.path.join(bc.BASE, "renders")
    os.makedirs(out_dir, exist_ok=True)
    stem = "%s_%d_" % ("".join(ch if ch.isalnum() else "_" for ch in tool), int(time.time()))
    res = _run("""
local src = T(%s)
local fr = %s or c.CurrentTime
local a0 = c:GetAttrs()
local rs, re = a0.COMPN_RenderStart, a0.COMPN_RenderEnd      -- Render() overwrites the comp's range: restore it
local sv = ADD("Saver")
sv:SetAttrs({ TOOLS_Name = "ZZ_BridgeRenderSaver" })
sv.Input = src.Output
sv.Clip[fu.TIME_UNDEFINED] = %s
sv.OutputFormat = "PNGFormat"
local ok = c:Render({ Start = fr, End = fr, Wait = true, Tool = sv })
local err = c:GetAttrs().COMPS_LastRenderError
sv:Delete()
c:SetAttrs({ COMPN_RenderStart = rs, COMPN_RenderEnd = re })
return { ok = ok, frame = fr, error = err }
""" % (L(tool), L(frame), L(os.path.join(out_dir, stem + ".png"))), timeout=600)
    png = os.path.join(out_dir, "%s%04d.png" % (stem, int(res["frame"])))
    if not os.path.exists(png):
        raise RuntimeError("render failed for %s at frame %s (%s). Typical causes: an upstream Text+ with an invalid "
                           "font/style pair, or a disconnected/empty input." % (tool, res.get("frame"), res.get("error")))
    meta = {"png": png, "frame": res["frame"], "tool": tool}
    try:
        from PIL import Image as PILImage
        im = PILImage.open(png)
        meta["size"] = list(im.size)
        if preview_width and im.width > preview_width:
            im = im.resize((preview_width, int(im.height * preview_width / im.width)))
        tmp = os.path.join(tempfile.gettempdir(), "fusion_bridge_preview.png")
        im.convert("RGBA").save(tmp)
        return [json.dumps(meta, ensure_ascii=False), Image(path=tmp)]
    except ImportError:
        return [json.dumps(meta, ensure_ascii=False), Image(path=png)]


# ---------------------------------------------------------------- Resolve level
@mcp.tool(annotations=RO)
def resolve_info() -> dict:
    """Resolve version, current page, project, timeline (name, fps, resolution, start/end, playhead) and the
    current Fusion comp name."""
    return bc.run(r"""
local r = { product = resolve:GetProductName(), version = resolve:GetVersionString(), page = resolve:GetCurrentPage() }
local p = resolve:GetProjectManager():GetCurrentProject()
if p then
  r.project = p:GetName()
  local tl = p:GetCurrentTimeline()
  if tl then
    r.timeline = { name = tl:GetName(), start = tl:GetStartFrame(), ["end"] = tl:GetEndFrame(),
                   playhead = tl:GetCurrentTimecode(), fps = tl:GetSetting("timelineFrameRate"),
                   width = tl:GetSetting("timelineResolutionWidth"), height = tl:GetSetting("timelineResolutionHeight"),
                   video_tracks = tl:GetTrackCount("video"), audio_tracks = tl:GetTrackCount("audio") }
  end
end
local cc = fu:GetCurrentComp()
r.comp = cc and cc:GetAttrs().COMPS_Name or nil
return r
""", 60).get("result")


@mcp.tool(annotations=EDIT)
def open_page(page: str) -> dict:
    """Switch Resolve's page: media, cut, edit, fusion, color, fairlight or deliver."""
    if page not in ("media", "cut", "edit", "fusion", "color", "fairlight", "deliver"):
        raise ValueError("unknown page")
    r = bc.run("return { ok = resolve:OpenPage(%s), page = resolve:GetCurrentPage() }" % L(page), 60)
    if not r.get("ok"):
        raise RuntimeError(r.get("error"))
    return r["result"]


if __name__ == "__main__":
    mcp.run()

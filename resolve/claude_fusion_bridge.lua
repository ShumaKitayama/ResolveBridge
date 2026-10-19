-- Claude Fusion Bridge v2 : file-in / log-out RPC for DaVinci Resolve 21.1 FREE (Fusion Lua sandbox)
--
-- Measured on Resolve 21.1.0 free / macOS: io, os.execute, ffi, bmd.writefile, UIManager and Resolve()
-- are unavailable to menu scripts. What remains: comp (Fusion API), fileexists, loadfile, wait, printerr.
--   IN : client writes  <BASE>/inbox/cmd_<session>_<n>.lua   (bridge polls with fileexists + loadfile)
--   OUT: bridge printerr()s  "CLAUDE_BR <session> <n> <part>/<total> <base64 json>"  into
--        ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/logs/davinci_resolve.log
-- Start once per Resolve session: Fusion page > Workspace > Scripts > claude_fusion_bridge
-- Runtime folder: $RESOLVE_BRIDGE_HOME/runtime, else <repo>/runtime (install.sh writes the repo path below), else ~/ResolveBridge/runtime
-- Stop: create <BASE>/stop_<session>  (the client does this with `bridge_client.py stop`)

local VERSION = "2.1.0"
local INSTALLED_ROOT = "@RESOLVE_BRIDGE_ROOT@"   -- replaced by install.sh with the clone's absolute path
local function getenv(k) return os.getenv and os.getenv(k) or nil end
local ROOT = getenv("RESOLVE_BRIDGE_HOME")
if not ROOT and not INSTALLED_ROOT:find("^@") then ROOT = INSTALLED_ROOT end
ROOT = ROOT or ((getenv("HOME") or "~") .. "/ResolveBridge")
local BASE = ROOT .. "/runtime"
local INBOX = BASE .. "/inbox"
local SESSION = tostring(os.time()) .. tostring(math.floor((os.clock() * 1000) % 1000))

local ESC = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
local function esc(s)
	return (s:gsub('[%c"\\]', function(c) return ESC[c] or string.format("\\u%04x", c:byte()) end))
end
local function tojson(v, depth, seen)
	depth = depth or 0; seen = seen or {}
	local t = type(v)
	if t == "nil" then return "null"
	elseif t == "boolean" then return tostring(v)
	elseif t == "number" then
		if v ~= v or v == math.huge or v == -math.huge then return '"' .. tostring(v) .. '"' end
		return string.format("%.10g", v)
	elseif t == "string" then return '"' .. esc(v) .. '"'
	elseif t == "table" then
		if seen[v] or depth > 10 then return '"<cycle/depth>"' end
		seen[v] = true
		local n, cnt = #v, 0
		for _ in pairs(v) do cnt = cnt + 1 end
		local parts = {}
		if n > 0 and cnt == n then
			for i = 1, n do parts[i] = tojson(v[i], depth + 1, seen) end
			seen[v] = nil
			return "[" .. table.concat(parts, ",") .. "]"
		end
		for k, val in pairs(v) do parts[#parts + 1] = '"' .. esc(tostring(k)) .. '":' .. tojson(val, depth + 1, seen) end
		seen[v] = nil
		return "{" .. table.concat(parts, ",") .. "}"
	end
	return '"' .. esc(tostring(v)) .. '"'
end

local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local function b64(s)
	local out, n = {}, #s
	for i = 1, n, 3 do
		local a, b, c = s:byte(i, i + 2)
		local x = a * 65536 + (b or 0) * 256 + (c or 0)
		local c1 = math.floor(x / 262144) % 64
		local c2 = math.floor(x / 4096) % 64
		local c3 = math.floor(x / 64) % 64
		local c4 = x % 64
		out[#out + 1] = B64:sub(c1 + 1, c1 + 1) .. B64:sub(c2 + 1, c2 + 1)
			.. (b and B64:sub(c3 + 1, c3 + 1) or "=") .. (c and B64:sub(c4 + 1, c4 + 1) or "=")
	end
	return table.concat(out)
end

local function emit(n, payload)
	local enc = b64(payload)
	local CH = 1400
	local total = math.max(1, math.ceil(#enc / CH))
	for p = 1, total do
		printerr("CLAUDE_BR " .. SESSION .. " " .. n .. " " .. p .. "/" .. total .. " " .. enc:sub((p - 1) * CH + 1, p * CH))
	end
end

local S = {}          -- persistent state shared by all commands in this session
local TOKEN = nil     -- set below from runtime/token.lua
local function scrub(m)
	m = tostring(m)
	if TOKEN then m = m:gsub(TOKEN, "<token>") end
	return m
end
local nextSeq = 1

local function runOne(n, path)
	local out = {}
	local function cprint(...)
		local a = {}
		for i = 1, select("#", ...) do a[i] = tostring((select(i, ...))) end
		out[#out + 1] = table.concat(a, "\t")
	end
	local cur = nil
	pcall(function() cur = fu:GetCurrentComp() end)
	local env = setmetatable({
		print = cprint, comp = cur or comp, fusion = fu, fu = fu, bmd = bmd, S = S,
		tojson = tojson, BRIDGE_BASE = BASE, SESSION = SESSION,
	}, { __index = _G })
	local t0 = os.clock()
	local chunk, err = loadfile(path)
	local res
	if not chunk then
		res = { ok = false, error = "compile: " .. scrub(err) }
	else
		setfenv(chunk, env)
		local r = { xpcall(chunk, function(e) return scrub(e) end) }
		if r[1] then res = { ok = true, result = r[2] } else res = { ok = false, error = r[2] } end
	end
	res.output = table.concat(out, "\n")
	res.cpu = os.clock() - t0
	local okj, js = pcall(tojson, res)
	if not okj then js = tojson({ ok = false, error = "json: " .. tostring(js), output = res.output }) end
	emit(n, js)
end

-- optional shared-secret auth: runtime/token.lua contains `return "<hex>"` (written by bridge_client.py, mode 600).
-- When present, only command files whose NAME carries the token are executed, so other local processes
-- cannot inject commands without reading that file. (io is unavailable in the free-edition sandbox; dofile works.)
do
	local tp = BASE .. "/token.lua"
	if fileexists(tp) then
		local ok, tk = pcall(dofile, tp)
		if ok and type(tk) == "string" and tk:match("^%x+$") and #tk >= 32 then TOKEN = tk end
	end
end
local function cmdPath(n)
	if TOKEN then return INBOX .. "/cmd_" .. SESSION .. "_" .. n .. "_" .. TOKEN .. ".lua" end
	return INBOX .. "/cmd_" .. SESSION .. "_" .. n .. ".lua"
end

printerr("CLAUDE_BR_START " .. SESSION .. " v" .. VERSION .. " auth=" .. (TOKEN and "on" or "off") .. " " .. BASE)

local stopFile = BASE .. "/stop_" .. SESSION
while true do
	local handled = 0
	while handled < 50 do
		local path = cmdPath(nextSeq)
		if not fileexists(path) then break end
		local n = nextSeq
		nextSeq = nextSeq + 1
		local ok, e = pcall(runOne, n, path)
		if not ok then emit(n, tojson({ ok = false, error = "bridge: " .. scrub(e) })) end
		handled = handled + 1
	end
	if fileexists(stopFile) then break end
	wait(0.1)
end

printerr("CLAUDE_BR_STOP " .. SESSION)

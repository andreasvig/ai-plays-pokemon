-- mGBA Socket Client for AI Plays Pokemon — slot 1
-- Static file. Load via mGBA Tools > Scripting > File > Load recent script.
-- PORT=8888, stream=/tmp/mgba_stream_1.png, screenshot=/tmp/mgba_screenshot_1.png
--
-- IMPORTANT: emu functions (addKey, clearKey, runFrame, screenshot, etc.)
-- can only be called from the frame callback context, not from socket handlers.
-- So we queue commands during socket reads and execute them in the frame callback.

local conn = nil
local PORT = 8888
local HOST = "127.0.0.1"

-- GBA button mapping
local BUTTONS = {
    A = 0,
    B = 1,
    SELECT = 2,
    START = 3,
    R = 4,
    L = 5,
    U = 6,
    D = 7,
    LB = 8,
    RB = 9
}

-- Command queue: commands read from socket, executed in frame callback
local command_queue = {}

-- Input queue for sequential button presses
local input_queue = {}
local queue_hold_frames = 12   -- 200ms hold — ensures walk, not just turn
local queue_gap_frames = 24    -- 400ms gap — walk animation is ~16 frames (267ms)
local queue_ab_hold_frames = 40 -- ~670ms hold for A/B — holding speeds up text scroll
local queue_ab_gap_frames = 20  -- ~330ms gap for A/B — wait for next dialogue box to appear
local queue_frame_counter = 0
local queue_state = "idle" -- "idle", "pressing", "waiting"

-- Per-input trace (src/referee/trace.py): Python sets a TRACESPEC of raw
-- memory ranges; after each input's gap the ranges are sampled and kept as
-- "name|hex;hex" rows until TRACE collects them. The bridge stays dumb — it
-- never knows what the bytes mean.
local trace_spec = {}   -- { {ptr=<u32 addr or nil>, off=<int>, addr=<int>, len=<int>}, ... }
local trace_rows = {}
local queue_last_name = nil

local function sample_range(entry)
    local base = entry.addr
    if entry.ptr ~= nil then
        local p = emu:read32(entry.ptr)
        if p < 0x02000000 or p >= 0x02040000 then return "" end
        base = p + entry.off
    end
    return tohex(emu:readRange(base, entry.len))
end

local function trace_sample(name)
    if #trace_spec == 0 then return end
    local parts = {}
    for i, entry in ipairs(trace_spec) do
        local ok, hex = pcall(sample_range, entry)
        parts[i] = ok and hex or ""
    end
    table.insert(trace_rows, (name or "?") .. "|" .. table.concat(parts, ";"))
end

-- Single press state
local press_key = nil
local press_frames_remaining = 0

-- Live stream: auto-capture screenshot for dashboard (separate from agent's CAP)
local stream_path = "/tmp/mgba_stream_1.png"
-- Publish every emulator frame. The recorder samples this 60fps source at
-- 30fps; source and sampler running at the same cadence phase-lock and
-- occasionally skip a version, while 60→30 gives every output tick a fresh
-- frame with timing headroom. Native 240×160 PNGs are only a few KB each.
local stream_interval = 1  -- every emulator frame = 60fps source
local stream_counter = 0

-- Send a response to the Python server
local function respond(msg)
    if conn then
        conn:send(msg .. "\n")
    end
end

-- Hex-encode a raw byte string (lowercase, two chars per byte)
local function tohex(bytes)
    local parts = {}
    for i = 1, #bytes do
        parts[i] = string.format("%02x", string.byte(bytes, i))
    end
    return table.concat(parts)
end

-- Execute a command in the frame callback context (safe to call emu functions)
local function execute_command(cmd)
    if cmd == nil or cmd == "" then
        return
    end
    cmd = cmd:match("^%s*(.-)%s*$")

    if cmd == "CAP" then
        local tmp_path = "/tmp/mgba_screenshot_1.png"
        emu:screenshot(tmp_path)
        respond("SCREENSHOT:" .. tmp_path)

    elseif cmd == "PAUSE" then
        respond("OK:Paused")

    elseif cmd == "UNPAUSE" then
        respond("OK:Unpaused")

    elseif cmd == "PING" then
        respond("PONG")

    elseif cmd:sub(1, 6) == "PRESS:" then
        local button_name = cmd:sub(7)
        local key = BUTTONS[button_name]
        if key == nil then
            respond("ERROR:Unknown button " .. button_name)
            return
        end
        -- Start pressing - will be held across frames
        press_key = key
        press_frames_remaining = queue_hold_frames
        emu:addKey(key)

    elseif cmd:sub(1, 4) == "SEQ:" then
        local sequence_str = cmd:sub(5)
        input_queue = {}
        for btn in string.gmatch(sequence_str, "([^;]+)") do
            local key = BUTTONS[btn]
            if key == nil then
                respond("ERROR:Unknown button in sequence: " .. btn)
                return
            end
            table.insert(input_queue, {key = key, name = btn})
        end
        queue_frame_counter = 0
        queue_state = "idle"
        respond("QUEUED:" .. #input_queue)

    elseif cmd:sub(1, 5) == "SAVE:" then
        local filepath = cmd:sub(6)
        local success = emu:saveStateFile(filepath)
        if success then
            respond("OK:State saved to " .. filepath)
        else
            respond("ERROR:Failed to save state to " .. filepath)
        end

    elseif cmd:sub(1, 5) == "LOAD:" then
        local filepath = cmd:sub(6)
        local success = emu:loadStateFile(filepath)
        if success then
            respond("OK:State loaded from " .. filepath)
        else
            respond("ERROR:Failed to load state from " .. filepath)
        end

    elseif cmd:sub(1, 10) == "TRACESPEC:" then
        -- TRACESPEC:<entry>;<entry>…  entry = <addr>:<len> | *<ptr>+<off>:<len>
        trace_spec = {}
        for item in string.gmatch(cmd:sub(11), "([^;]+)") do
            local ptr_s, off_s, len_s = item:match("^%*([^%+]+)%+([^:]+):(.+)$")
            if ptr_s then
                table.insert(trace_spec, {ptr = tonumber(ptr_s), off = tonumber(off_s), len = tonumber(len_s)})
            else
                local addr_s, l_s = item:match("^([^:]+):(.+)$")
                if addr_s then
                    table.insert(trace_spec, {addr = tonumber(addr_s), off = 0, len = tonumber(l_s)})
                end
            end
        end
        trace_rows = {}
        respond("OK:tracespec=" .. #trace_spec)

    elseif cmd == "TRACE" then
        -- Rows since the last TRACE, "/"-joined; cleared on read.
        respond("TRACE:" .. table.concat(trace_rows, "/"))
        trace_rows = {}

    elseif cmd:sub(1, 8) == "READMEM:" then
        -- READMEM:<addr>:<len> — addr/len decimal or 0x-prefixed hex.
        -- Stay dumb: read raw bytes at addr, hex-encode, respond. No game knowledge.
        local addr_s, len_s = cmd:sub(9):match("([^:]+):(.+)")
        local addr = addr_s and tonumber(addr_s)
        local len = len_s and tonumber(len_s)
        if not addr or not len then
            respond("ERROR:Invalid READMEM format. Use READMEM:addr:len")
            return
        end
        local bytes = emu:readRange(addr, len)
        respond("MEM:" .. tohex(bytes))

    elseif cmd:sub(1, 7) == "CONFIG:" then
        local param, value = cmd:sub(8):match("([^=]+)=(.+)")
        if param and value then
            local val = tonumber(value)
            if not val then
                respond("ERROR:Invalid value " .. value)
                return
            end
            if param == "hold_frames" then
                queue_hold_frames = val
                respond("OK:hold_frames=" .. val)
            elseif param == "gap_frames" then
                queue_gap_frames = val
                respond("OK:gap_frames=" .. val)
            elseif param == "ab_hold_frames" then
                queue_ab_hold_frames = val
                respond("OK:ab_hold_frames=" .. val)
            elseif param == "ab_gap_frames" then
                queue_ab_gap_frames = val
                respond("OK:ab_gap_frames=" .. val)
            else
                respond("ERROR:Unknown config param " .. param)
            end
        else
            respond("ERROR:Invalid config format. Use CONFIG:param=value")
        end

    else
        respond("ERROR:Unknown command: " .. cmd)
    end
end

-- Process one frame of the input queue
local function process_queue()
    if #input_queue == 0 and queue_state ~= "waiting" then
        return
    end

    if queue_state == "idle" then
        local key = input_queue[1].key
        local is_ab = (key == BUTTONS.A or key == BUTTONS.B)
        queue_current_hold = is_ab and queue_ab_hold_frames or queue_hold_frames
        emu:addKey(key)
        queue_state = "pressing"
        queue_frame_counter = 0
    elseif queue_state == "pressing" then
        queue_frame_counter = queue_frame_counter + 1
        if queue_frame_counter >= (queue_current_hold or queue_hold_frames) then
            local item = table.remove(input_queue, 1)
            emu:clearKey(item.key)
            queue_last_name = item.name
            -- Use longer gap after A/B presses (wait for next dialogue box).
            -- The LAST input waits its gap too (2026-09-14): the walk animation
            -- (~16 frames) must finish before the trace samples the tile, and
            -- SEQUENCE_DONE moves to the end of that gap — inside the sleep the
            -- Python side already computes from hold+gap per button.
            local is_ab = (item.key == BUTTONS.A or item.key == BUTTONS.B)
            local gap = is_ab and queue_ab_gap_frames or queue_gap_frames
            queue_state = "waiting"
            queue_frame_counter = 0
            queue_current_gap = gap
        end
    elseif queue_state == "waiting" then
        queue_frame_counter = queue_frame_counter + 1
        if queue_frame_counter >= (queue_current_gap or queue_gap_frames) then
            trace_sample(queue_last_name)
            queue_state = "idle"
            if #input_queue == 0 then
                respond("SEQUENCE_DONE")
            end
        end
    end
end

-- Process single button press across frames
local function process_press()
    if press_key == nil then
        return
    end
    press_frames_remaining = press_frames_remaining - 1
    if press_frames_remaining <= 0 then
        emu:clearKey(press_key)
        press_key = nil
        respond("OK")
    end
end

-- Read commands from socket into the command queue (no emu calls here)
local function poll_commands()
    if conn == nil then
        return
    end
    while conn:hasdata() do
        local data, err = conn:receive(4096)
        if data then
            for line in data:gmatch("([^\n]+)") do
                table.insert(command_queue, line)
            end
        elseif err == socket.ERRORS.AGAIN then
            -- Data not ready yet, try next frame
            break
        else
            console:log("Server disconnected: " .. tostring(err))
            conn:close()
            conn = nil
            return
        end
    end
end

-- Connect to the Python server
local function connect_to_server()
    console:log("Connecting to " .. HOST .. ":" .. PORT .. "...")
    conn = socket.connect(HOST, PORT)
    if conn then
        console:log("Connected to Python harness at " .. HOST .. ":" .. PORT)
        conn:send("HELLO\n")
    else
        console:error("Failed to connect to " .. HOST .. ":" .. PORT)
        console:error("Make sure the Python harness is running first!")
    end
end

-- Main frame callback
callbacks:add("frame", function()
    if conn ~= nil then
        -- Read any pending commands from socket into queue
        poll_commands()

        -- Execute queued commands (now safe to call emu functions)
        while #command_queue > 0 do
            local cmd = table.remove(command_queue, 1)
            execute_command(cmd)
        end

        -- Process ongoing button press
        process_press()

        -- Process ongoing sequence
        process_queue()
    end

    -- Auto-capture for live dashboard stream (runs AFTER game logic)
    stream_counter = stream_counter + 1
    if stream_counter >= stream_interval then
        pcall(emu.screenshot, emu, stream_path)
        stream_counter = 0
    end
end)

-- Start connection
connect_to_server()

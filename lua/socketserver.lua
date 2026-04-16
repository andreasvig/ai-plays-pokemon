-- mGBA Socket Client for AI Plays Pokemon
-- Connects to the Python harness TCP server and processes commands each frame.
-- Protocol: newline-delimited commands and responses.
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

-- Single press state
local press_key = nil
local press_frames_remaining = 0

-- Live stream: auto-capture screenshot for dashboard (separate from agent's CAP)
local stream_path = "/tmp/mgba_stream.png"
local stream_interval = 4  -- every 4 frames ≈ 15fps at 60fps
local stream_counter = 0

-- Send a response to the Python server
local function respond(msg)
    if conn then
        conn:send(msg .. "\n")
    end
end

-- Execute a command in the frame callback context (safe to call emu functions)
local function execute_command(cmd)
    if cmd == nil or cmd == "" then
        return
    end
    cmd = cmd:match("^%s*(.-)%s*$")

    if cmd == "CAP" then
        local tmp_path = "/tmp/mgba_screenshot.png"
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
            table.insert(input_queue, key)
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
    if #input_queue == 0 then
        return
    end

    if queue_state == "idle" then
        local key = input_queue[1]
        local is_ab = (key == BUTTONS.A or key == BUTTONS.B)
        queue_current_hold = is_ab and queue_ab_hold_frames or queue_hold_frames
        emu:addKey(key)
        queue_state = "pressing"
        queue_frame_counter = 0
    elseif queue_state == "pressing" then
        queue_frame_counter = queue_frame_counter + 1
        if queue_frame_counter >= (queue_current_hold or queue_hold_frames) then
            local key = table.remove(input_queue, 1)
            emu:clearKey(key)
            -- Use longer gap after A/B presses (wait for next dialogue box)
            local is_ab = (key == BUTTONS.A or key == BUTTONS.B)
            local gap = is_ab and queue_ab_gap_frames or queue_gap_frames
            queue_state = "waiting"
            queue_frame_counter = 0
            queue_current_gap = gap
            if #input_queue == 0 then
                queue_state = "idle"
                respond("SEQUENCE_DONE")
            end
        end
    elseif queue_state == "waiting" then
        queue_frame_counter = queue_frame_counter + 1
        if queue_frame_counter >= (queue_current_gap or queue_gap_frames) then
            queue_state = "idle"
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

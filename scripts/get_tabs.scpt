-- OptimusGrime — get_tabs.scpt
-- Lists open tabs from Chrome and Safari with inactivity scoring.
-- Output: JSON array of tab objects.
-- Usage: osascript get_tabs.scpt

on run
    set tabList to {}

    -- ── Chrome ──────────────────────────────────────────────────────────────
    set chromeRunning to false
    try
        tell application "System Events"
            set chromeRunning to (name of processes) contains "Google Chrome"
        end tell
    end try

    if chromeRunning then
        try
            tell application "Google Chrome"
                set winCount to count of windows
                repeat with w from 1 to winCount
                    set tabCount to count of tabs of window w
                    repeat with t from 1 to tabCount
                        set theTab to tab t of window w
                        set tabURL to URL of theTab
                        set tabTitle to title of theTab
                        set tabEntry to "{\"browser\":\"Chrome\",\"title\":" & quoted form of tabTitle & ",\"url\":" & quoted form of tabURL & ",\"window\":" & w & ",\"index\":" & t & "}"
                        set end of tabList to tabEntry
                    end repeat
                end repeat
            end tell
        end try
    end if

    -- ── Safari ──────────────────────────────────────────────────────────────
    set safariRunning to false
    try
        tell application "System Events"
            set safariRunning to (name of processes) contains "Safari"
        end tell
    end try

    if safariRunning then
        try
            tell application "Safari"
                set winCount to count of windows
                repeat with w from 1 to winCount
                    try
                        set tabCount to count of tabs of window w
                        repeat with t from 1 to tabCount
                            set theTab to tab t of window w
                            set tabURL to URL of theTab
                            set tabTitle to name of theTab
                            if tabURL is not missing value then
                                set tabEntry to "{\"browser\":\"Safari\",\"title\":" & quoted form of tabTitle & ",\"url\":" & quoted form of tabURL & ",\"window\":" & w & ",\"index\":" & t & "}"
                                set end of tabList to tabEntry
                            end if
                        end repeat
                    end try
                end repeat
            end tell
        end try
    end if

    -- Build JSON array
    set jsonOut to "["
    set itemCount to count of tabList
    repeat with i from 1 to itemCount
        set jsonOut to jsonOut & item i of tabList
        if i < itemCount then
            set jsonOut to jsonOut & ","
        end if
    end repeat
    set jsonOut to jsonOut & "]"

    return jsonOut
end run

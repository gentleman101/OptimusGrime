-- OptimusGrime — get_tabs.scpt
-- Lists open tabs from Chrome and Safari with inactivity scoring.
-- Output: valid JSON array of tab objects.
-- Usage: osascript get_tabs.scpt

-- Escape a string for JSON double-quoted values
on jsonEscape(s)
    set s to s as string
    set res to ""
    repeat with c in characters of s
        set c to c as string
        if c = "\"" then
            set res to res & "\\\""
        else if c = "\\" then
            set res to res & "\\\\"
        else if c = "/" then
            set res to res & "/"
        else
            set res to res & c
        end if
    end repeat
    return res
end jsonEscape

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
                    set tabCount to count of (tabs of window w)
                    repeat with t from 1 to tabCount
                        set theTab to item t of (tabs of window w)
                        set tabURL to my jsonEscape(URL of theTab)
                        set tabTitle to my jsonEscape(title of theTab)
                        set tabEntry to "{\"browser\":\"Chrome\",\"title\":\"" & tabTitle & "\",\"url\":\"" & tabURL & "\",\"window\":" & w & ",\"index\":" & t & "}"
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
                            if tabURL is not missing value then
                                set tabURL to my jsonEscape(tabURL)
                                set tabTitle to my jsonEscape(name of theTab)
                                set tabEntry to "{\"browser\":\"Safari\",\"title\":\"" & tabTitle & "\",\"url\":\"" & tabURL & "\",\"window\":" & w & ",\"index\":" & t & "}"
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

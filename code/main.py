# Required libraries for the project
import gc
import json
import os
import select
import sys
import time

import machine
import network      # For WiFi functionality
import urequests    # For making HTTP requests
from machine import Pin

# GPIO Pin definitions (ESP32-C3 SuperMini, see schematic)
NO_INDICATOR = Pin(1, Pin.OUT)    # "no" bulb, BSS138 low-side switch: on() lights it
YES_INDICATOR = Pin(2, Pin.OUT)   # "yes" bulb, BSS138 low-side switch: on() lights it
PWR_EN = Pin(3, Pin.OUT)          # LT8361 91 V boost enable, active HIGH (R7 pulls it low)
LED_W = Pin(7, Pin.OUT)           # White LED: solid = live data, blinking = bad data, off = offline/stale
# SW1 pulls this to ground; R1 (20k) is the pull-up. The internal pull-up is
# just belt and braces. GPIO9 is also the BOOT strap: holding SW1 at power-up
# enters the bootloader.
BIST_EN = Pin(9, Pin.IN, Pin.PULL_UP)

# URL for the JSON data containing nuclear event status
JSON_URL = "https://raw.githubusercontent.com/bigcrimping/ned_json/main/events.json"
STATUS_KEY = "nuke gone off?"

# WiFi credentials written over USB serial live here (see save_wifi()).
# secrets.py is still read as a fallback if this file does not exist.
CREDS_FILE = "wifi.json"

# Timing
UPDATE_INTERVAL_MS = 60_000      # how often to poll the JSON (it changes rarely)
HTTP_TIMEOUT_S = 15
WIFI_TIMEOUT_S = 20              # per connection attempt
WIFI_BOOT_ATTEMPTS = 3           # then reset and start again
BOOT_FAIL_WAIT_MS = 10_000       # time to type 'wifi' before the reset
STALE_AFTER_FAILURES = 3         # consecutive failures before the white LED goes off
BLANK_AFTER_FAILURES = 30        # ~30 min with no good data: stop showing the old answer
BIST_MS = 3_000
FAULT_BLINK_MS = 500
WDT_TIMEOUT_MS = 60_000          # must exceed the longest blocking call (DNS/TLS/HTTP)
PROMPT_TIMEOUT_S = 120

# Runtime state, module level so the 'status' serial command can report it
state = {
    "shown": None,          # what the bulbs show: "yes", "no" or None (both off)
    "failures": 0,          # consecutive failed fetches
    "fault": False,         # last fetch parsed but did not say yes/no
    "stale": False,         # STALE_AFTER_FAILURES reached
    "ssid": None,
    "wlan": None,
}

wdt = None
bist_triggered = False
bist_armed = True       # False after a test until SW1 is seen released (stops release bounce re-triggering)


# --------------------------------------------------------------------------
# Hardware helpers
# --------------------------------------------------------------------------

def bist_interrupt_handler(pin):
    """Falling edge on SW1. Ignored until the button has been released after a test."""
    global bist_triggered
    if bist_armed:
        bist_triggered = True


BIST_EN.irq(trigger=Pin.IRQ_FALLING, handler=bist_interrupt_handler)


def show(status):
    """Drive the bulbs for "yes", "no" or None (both off). Never both on."""
    NO_INDICATOR.value(1 if status == "no" else 0)
    YES_INDICATOR.value(1 if status == "yes" else 0)


def safe_off():
    """91 V supply off, bulbs off, LED off."""
    PWR_EN.off()
    show(None)
    LED_W.off()


def feed_wdt():
    if wdt is not None:
        wdt.feed()


def idle(ms):
    """Wait ms while feeding the watchdog and servicing serial commands."""
    deadline = time.ticks_add(time.ticks_ms(), ms)
    while True:
        feed_wdt()
        check_serial()
        remaining = time.ticks_diff(deadline, time.ticks_ms())
        if remaining <= 0:
            return
        time.sleep_ms(min(remaining, 50))


# --------------------------------------------------------------------------
# USB serial console: type 'wifi' + Enter to set credentials
# --------------------------------------------------------------------------

_poll = select.poll()
_poll.register(sys.stdin, select.POLLIN)
_prev_cr = False
_cmd = b""


def _read_char(timeout_ms=0):
    """One byte from USB serial, or None. CR, LF and CRLF all come back as b'\\n'."""
    global _prev_cr
    while _poll.poll(timeout_ms):
        ch = sys.stdin.buffer.read(1)
        if not ch:
            return None
        if ch == b"\n" and _prev_cr:
            _prev_cr = False
            timeout_ms = 0
            continue
        _prev_cr = ch == b"\r"
        return b"\n" if _prev_cr else ch
    return None


def _prompt(label, secret=False, timeout_s=PROMPT_TIMEOUT_S):
    """Read a line from serial, echoing it (or '*'). Returns str, or None on timeout."""
    sys.stdout.write(label)
    buf = b""
    deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        feed_wdt()
        ch = _read_char(100)
        if ch is None:
            continue
        if ch == b"\n":
            sys.stdout.write("\n")
            try:
                return buf.decode("utf-8")
            except UnicodeError:
                print("Invalid characters, try again.")
                return None
        if ch in (b"\x08", b"\x7f"):
            if buf:
                # drop a whole UTF-8 character, not just its last byte
                buf = buf[:-1]
                while buf and (buf[-1] & 0xC0) == 0x80:
                    buf = buf[:-1]
                sys.stdout.write("\b \b")
        elif ch[0] >= 0x20 and ch[0] != 0x7F and len(buf) < 96:
            buf += ch
            if secret:
                if ch[0] < 0x80 or ch[0] >= 0xC0:  # one '*' per character
                    sys.stdout.write("*")
            elif ch[0] < 0x80:
                sys.stdout.write(ch.decode())
            elif ch[0] >= 0xC0:                    # non-ASCII lead byte
                sys.stdout.write("?")
    sys.stdout.write("\nTimed out.\n")
    return None


def _validate_wifi(ssid, password):
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise ValueError("SSID must be 1-32 bytes")
    if not isinstance(password, str):
        raise ValueError("password must be a string")
    n = len(password)
    if n == 64:
        try:
            int(password, 16)
        except ValueError:
            raise ValueError("a 64-character password must be hex")
    elif n != 0 and not 8 <= n <= 63:
        raise ValueError("password must be empty (open network) or 8-63 characters")


def save_wifi(ssid, password=""):
    """
    Store WiFi credentials in flash (wifi.json). Takes effect on next boot.
    Also usable non-interactively, e.g.
        mpremote exec "import main; main.save_wifi('MyNet', 'secret')" reset
    """
    _validate_wifi(ssid, password)
    tmp = CREDS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"ssid": ssid, "password": password}, f)
    try:
        os.rename(tmp, CREDS_FILE)
    except OSError:
        # filesystems that will not rename over an existing file
        try:
            os.remove(CREDS_FILE)
        except OSError:
            pass
        os.rename(tmp, CREDS_FILE)
    print("Saved WiFi credentials for '%s'" % ssid)


def load_credentials():
    """(ssid, password) from wifi.json, else secrets.py, else (None, None)."""
    try:
        with open(CREDS_FILE) as f:
            creds = json.load(f)
        _validate_wifi(creds["ssid"], creds.get("password", ""))
        return creds["ssid"], creds.get("password", "")
    except OSError:
        pass                                    # no file yet
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        print("Ignoring bad %s:" % CREDS_FILE, e)
    try:
        import secrets
        return secrets.SSID, secrets.PASSWORD
    except (ImportError, AttributeError):
        return None, None


def provision_wifi():
    """Interactive: ask for SSID/password over serial, save, restart."""
    print("\n--- WiFi setup (Enter on an empty SSID to cancel) ---")
    ssid = _prompt("SSID: ")
    if not ssid:
        print("Cancelled.")
        return
    password = _prompt("Password (empty for an open network): ", secret=True)
    if password is None:
        print("Cancelled.")
        return
    try:
        _validate_wifi(ssid, password)
    except ValueError as e:
        print("Not saved:", e)
        return
    answer = _prompt("Save '%s' and restart? [y/N]: " % ssid)
    if not answer or answer.strip().lower() not in ("y", "yes"):
        print("Not saved.")
        return
    save_wifi(ssid, password)
    print("Restarting...")
    safe_off()
    time.sleep_ms(200)
    machine.reset()


def print_status():
    wlan = state["wlan"]
    connected = wlan is not None and wlan.isconnected()
    print("SSID:     ", state["ssid"])
    print("WiFi:     ", ("connected " + wlan.ifconfig()[0]) if connected else "not connected")
    print("Showing:  ", state["shown"] or "nothing")
    print("Failures: ", state["failures"], "(bad data)" if state["fault"] else "")


def print_help():
    print("Commands: wifi (set WiFi credentials), status, help")


def check_serial():
    """Non-blocking: collect a typed command line and run it on Enter."""
    global _cmd
    while True:
        ch = _read_char(0)
        if ch is None:
            return
        if ch == b"\n":
            sys.stdout.write("\n")
            cmd, _cmd = _cmd.decode().strip().lower(), b""
            if cmd == "wifi":
                provision_wifi()
            elif cmd == "status":
                print_status()
            elif cmd:
                print_help()
        elif ch in (b"\x08", b"\x7f"):
            if _cmd:
                _cmd = _cmd[:-1]
                sys.stdout.write("\b \b")
        elif 0x20 <= ch[0] < 0x7F and len(_cmd) < 16:
            _cmd += ch
            sys.stdout.write(ch.decode())


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------

def connect_wifi(wlan, ssid, password, timeout_s=WIFI_TIMEOUT_S):
    """
    Try to bring the WiFi link up, blinking LED_W while waiting.
    Returns:
        bool: True if connected
    """
    if wlan.isconnected():
        return True
    print("Connecting to WiFi '%s'..." % ssid)
    try:
        wlan.active(True)
        # connect() while the driver is still auto-reconnecting raises
        # "Wifi Internal State Error" on ESP32, so stop it first.
        try:
            wlan.disconnect()
        except OSError:
            pass
        wlan.connect(ssid, password)
    except OSError as e:
        print("WiFi connect error:", e)
        LED_W.off()
        return False
    deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
    led = 0
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), deadline) > 0:
            print("WiFi connection failed")
            LED_W.off()
            return False
        led ^= 1
        LED_W.value(led)
        idle(250)
    print("WiFi connected", wlan.ifconfig()[0])
    LED_W.on()
    return True


def get_json_data():
    """
    Fetches and parses JSON data from the remote URL
    Returns:
        dict: Parsed JSON data if successful, None if failed
    """
    response = None
    gc.collect()                    # TLS needs a big contiguous block on the C3
    try:
        headers = {"User-Agent": "YesNo"}
        response = urequests.get(JSON_URL, headers=headers, timeout=HTTP_TIMEOUT_S)
        if response.status_code == 200:
            return response.json()
        print("HTTP error code:", response.status_code)
    except Exception as e:
        print("Error fetching JSON data:", e)
    finally:
        if response is not None:
            response.close()
    return None


def parse_status(data):
    """
    Only the exact strings "yes" and "no" are trusted. Anything else (missing
    key, null, a changed feed, an error page that happened to parse) gives
    None; a false "yes" is the one thing this device must never show.
    """
    status = data.get(STATUS_KEY) if isinstance(data, dict) else None
    return status if status in ("yes", "no") else None


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def main():
    global wdt, bist_triggered, bist_armed

    # Boot: 91 V supply off and both bulbs off until we are online
    safe_off()
    print("\nYes/No indicator. Type 'wifi' + Enter to set WiFi credentials.")
    wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)

    ssid, password = load_credentials()
    state["ssid"] = ssid
    if not ssid:
        print("No WiFi credentials stored. Type 'wifi' + Enter to set them.")
        while True:
            idle(1000)                  # provisioning resets the board

    wlan = network.WLAN(network.STA_IF)
    state["wlan"] = wlan
    for attempt in range(WIFI_BOOT_ATTEMPTS):
        if connect_wifi(wlan, ssid, password):
            break
        print("WiFi attempt %d of %d failed" % (attempt + 1, WIFI_BOOT_ATTEMPTS))
    else:
        print("Could not connect to WiFi. Resetting in %d s; type 'wifi' + Enter "
              "to change credentials." % (BOOT_FAIL_WAIT_MS // 1000))
        idle(BOOT_FAIL_WAIT_MS)
        machine.reset()

    # Enable the bulb supply now that we can get data
    PWR_EN.on()

    # Fetch immediately on the first pass
    last_update = time.ticks_add(time.ticks_ms(), -UPDATE_INTERVAL_MS)
    last_blink = time.ticks_ms()
    led = 0
    released_samples = 0

    while True:
        if bist_triggered:
            print("BIST: both indicators ON for %d s" % (BIST_MS // 1000))
            NO_INDICATOR.on()
            YES_INDICATOR.on()
            idle(BIST_MS)
            show(state["shown"])        # back to exactly what was shown before
            bist_armed = False
            bist_triggered = False      # also swallows bounces during the test
            released_samples = 0

        # Re-arm once SW1 reads released on two loop passes in a row (~100 ms
        # apart), so contact bounce when a long press is let go is ignored.
        if not bist_armed:
            released_samples = released_samples + 1 if BIST_EN.value() else 0
            if released_samples >= 2:
                bist_armed = True

        if time.ticks_diff(time.ticks_ms(), last_update) >= UPDATE_INTERVAL_MS:
            last_update = time.ticks_ms()

            if not wlan.isconnected():
                print("WiFi link down, reconnecting")
                LED_W.off()
                connect_wifi(wlan, ssid, password)

            data = get_json_data() if wlan.isconnected() else None
            status = parse_status(data)
            if status is not None:
                if status != state["shown"]:
                    print("Status:", status)
                state["shown"] = status
                show(status)
                state["failures"] = 0
                state["fault"] = False
                state["stale"] = False
                LED_W.on()
            else:
                state["failures"] += 1
                state["fault"] = data is not None   # parsed, but not yes/no
                if state["fault"]:
                    print("Unexpected status in JSON:",
                          data.get(STATUS_KEY) if isinstance(data, dict) else data)
                    state["shown"] = None
                    show(None)
                if state["failures"] >= STALE_AFTER_FAILURES:
                    state["stale"] = True
                if state["failures"] >= BLANK_AFTER_FAILURES and state["shown"] is not None:
                    print("No good data for %d polls, blanking bulbs" % state["failures"])
                    state["shown"] = None
                    show(None)
                print("Fetch failed (%d in a row)" % state["failures"])

        # White LED: solid = live, blinking = bad data, off = stale/offline
        now = time.ticks_ms()
        if state["fault"]:
            if time.ticks_diff(now, last_blink) >= FAULT_BLINK_MS:
                led ^= 1
                LED_W.value(led)
                last_blink = now
        elif state["stale"] or not wlan.isconnected():
            LED_W.off()

        idle(100)


def _keep_wdt_fed():
    """After Ctrl-C, keep feeding the watchdog so the REPL stays usable."""
    if wdt is None:
        return
    try:
        machine.Timer(0).init(period=WDT_TIMEOUT_MS // 4, callback=lambda t: wdt.feed())
    except Exception as e:
        print("Could not keep watchdog fed, board will reset:", e)


def run():
    try:
        main()
    except KeyboardInterrupt:
        safe_off()
        _keep_wdt_fed()
        print("Stopped: 91 V supply off. machine.reset() to restart.")
    except Exception as e:
        safe_off()
        print("Fatal error, restarting in 5 s:")
        sys.print_exception(e)
        time.sleep(5)
        machine.reset()


# Program entry point (importing main, e.g. for save_wifi(), does not start it)
if __name__ == "__main__":
    run()

# hasanukegoneoff.com Indicator

![yesno_web](https://github.com/user-attachments/assets/6621fc59-1d01-40b9-91f1-1ddbc34d36b6)

This device uses a ESP32 to interogate the status of [hasanukegoneoff.com](https://www.hasanukegoneoff.com) and turns on either the yes or no bulb depending on the answer.

The buibs are INS-1 neon style bulbs, often found NOS and still available (as of writing) from Ukraine

# SAFETY NOTE

This board contains a 91V power supply, this has every opportunity to hurt or even kill you. If you are not trained in high voltage safety please don't build this.

# Software Setup

To setup the ESP32, load MicroPython from https://micropython.org/download/

Once loaded, copy `main.py` onto the board (for example with Thonny, or `mpremote cp code/main.py :main.py`).

## Setting the WiFi details over USB

Open a serial terminal to the board (Thonny's shell, `mpremote`, PuTTY, etc. at 115200 baud), type `wifi` and press Enter:

```
wifi
--- WiFi setup (Enter on an empty SSID to cancel) ---
SSID: MyNetwork
Password (empty for an open network): ********
Save 'MyNetwork' and restart? [y/N]: y
```

The details are saved to `wifi.json` on the board and it restarts. You can do this at any time: while it is running, while it is trying to connect, or during the 10 seconds before it restarts after failing to connect. If no WiFi details are stored at all, it waits for you to enter them. Other commands are `status` and `help`.

To set them without typing at the prompt:

```bash
mpremote exec "import main; main.save_wifi('MyNetwork', 'password')" reset
```

`secrets.py` still works as a fallback: if there is no `wifi.json`, the SSID and password in `secrets.py` are used. Don't commit your real password to `secrets.py`.

## Behaviour

- The 91V supply is only switched on once WiFi is connected. A bulb is only lit when the feed says exactly `"yes"` or `"no"`.
- White LED: solid = live data, blinking = the feed returned something unexpected (both bulbs off), off = offline (after 3 failed polls in a row).
- If there's no good data for about 30 minutes, both bulbs switch off so an old answer isn't shown forever.
- A hardware watchdog restarts the board if it hangs. If you stop the program with Ctrl-C, the 91V supply is switched off and the watchdog keeps being fed so you can use the REPL. A soft reset from a tool may still let the watchdog restart the board about a minute later. That's expected.

# Mechanical Build

![Build_Instructions](https://github.com/user-attachments/assets/df9f7fe0-4319-4a45-a47f-980be230ea4d)

# PCB Build

The PCB is a simple double-sided board. The Gerbers are in the relevant folder and should be easy to produce by any PCB vendor.

![image](https://github.com/user-attachments/assets/908cd805-1d59-4072-98c1-91426c29486f)

![image](https://github.com/user-attachments/assets/e2d32583-fc47-4625-ab04-21b87085c7ce)


# BIST (Built in self test)

To confirm the bulbs are operating correctly and BIST function is included

The SW1 switch triggers this functionality, the illuminates both bulbs at once


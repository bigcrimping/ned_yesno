# Required libraries for the project
import network      # For WiFi functionality
import urequests   # For making HTTP requests
import json        # For parsing JSON data
import time        # For delays and timing
from machine import Pin  # For GPIO control
import secrets     # Import secrets configuration

# GPIO Pin definitions
# Each pin is configured for its specific purpose
NO_INDICATOR = Pin(1, Pin.OUT)    # LED indicator for "no" nuclear event state
YES_INDICATOR = Pin(2, Pin.OUT)   # LED indicator for "yes" nuclear event state
PWR_EN = Pin(3, Pin.OUT)         # Power enable pin for the indicator power supply
LED_W = Pin(7, Pin.OUT)          # White LED for WiFi status indication
# Modified BIST pin to use interrupts
BIST_EN = Pin(9, Pin.IN)  # Built-In Self Test enable pin with pull-up

# URL for the JSON data containing nuclear event status
# Using GitHub API URL constructed from secrets
JSON_URL = f"https://raw.githubusercontent.com/bigcrimping/ned_json/main/events.json"

# Global variable to store last JSON data
last_json_data = None
# Global flag for BIST
bist_triggered = False

def bist_interrupt_handler(pin):
    """
    Interrupt handler for BIST functionality
    Triggered on falling edge of BIST_EN pin
    """
    global bist_triggered
    bist_triggered = True

        

# Configure interrupt on falling edge
BIST_EN.irq(trigger=Pin.IRQ_FALLING, handler=bist_interrupt_handler)

def connect_wifi():
    """
    Establishes WiFi connection and manages the status LED
    Returns:
        bool: True if connection successful, False otherwise
    """
    wlan = network.WLAN(network.STA_IF)  # Initialize WiFi in station mode
    wlan.active(True)                    # Activate the WiFi interface
    
    if not wlan.isconnected():
        print('Connecting to WiFi...')
        wlan.connect(secrets.SSID, secrets.PASSWORD)
        
        # Wait for connection with 10-second timeout
        max_wait = 10
        while max_wait > 0:
            if wlan.isconnected():
                break
            max_wait -= 1
            print('Waiting for connection...')
            # Visual connection attempt indication
            for led in [LED_W, NO_INDICATOR, YES_INDICATOR]:
                led.on()
            time.sleep(0.5)
            for led in [LED_W, NO_INDICATOR, YES_INDICATOR]:
                led.off()
            time.sleep(0.5)
    
    # Update LED and return connection status
    if wlan.isconnected():
        print('WiFi connected')
        LED_W.on()  # Turn on LED to show successful connection
        NO_INDICATOR.off()  # Ensure other LEDs are off
        YES_INDICATOR.off()
        return True
    else:
        print('WiFi connection failed')
        LED_W.off()  # Keep LED off to indicate connection failure
        NO_INDICATOR.off()
        YES_INDICATOR.off()
        return False

def get_json_data():
    """
    Fetches and parses JSON data from the remote URL
    Returns:
        dict: Parsed JSON data if successful, None if failed
    """
    try:
        headers = {
            "User-Agent": f"YesNo"
        }
        response = urequests.get(JSON_URL, headers=headers)
        if response.status_code == 200:
            data = response.json()
            response.close()
            global last_json_data
            last_json_data = data
            return data
        else:
            print("HTTP error code:", response.status_code)
            response.close()
    except Exception as e:
        print('Error fetching JSON data:', e)
    return None


def update_indicators(data):
    """
    Updates the LED indicators based on nuclear event status
    Args:
        data (dict): JSON data containing nuclear event status
    """
    if data and 'nuke gone off?' in data:
        status = data['nuke gone off?']
        # Set indicators based on status:
        # NO_INDICATOR on when status is "no"
        # YES_INDICATOR on when status is "yes"
        if status == "no":
            NO_INDICATOR.on()
            YES_INDICATOR.off()
            print("No")
        else:
            NO_INDICATOR.off()
            YES_INDICATOR.on()
            print("No")

def main():
    """
    Main program loop
    Initializes system, manages WiFi connection,
    and continuously monitors nuclear event status
    """
    global bist_triggered
    # Initial system setup
    PWR_EN.on()  # Ensure power supply starts disabled
    NO_INDICATOR.on()  # Initialize indicators to off state
    YES_INDICATOR.on()
    
    # Establish WiFi connection
    if not connect_wifi():
        return  # Exit if WiFi connection fails
    
    # Enable power supply for indicators
    PWR_EN.on()
    
    # Main operation loop
    update_interval = 00000  # 60 seconds in milliseconds
    last_update = time.ticks_ms()
    while True:
        if bist_triggered:
            print('BIST mode active: both indicators ON for 3 seconds')
            NO_INDICATOR.on()
            YES_INDICATOR.on()
            time.sleep(3)
            # Return to normal operation by updating with last known data
            if last_json_data:
                update_indicators(last_json_data)
            bist_triggered = False
        else:
            now = time.ticks_ms()
            if time.ticks_diff(now, last_update) >= update_interval:
                # Fetch and process latest nuclear event data
                data = get_json_data()
                if data:
                    update_indicators(data)
                last_update = now
        # Small sleep to avoid busy-waiting
        time.sleep_ms(100)

# Program entry point
if __name__ == '__main__':
    main()



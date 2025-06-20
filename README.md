# hasanukegoneoff.com Indicator

![yesno_web](https://github.com/user-attachments/assets/6621fc59-1d01-40b9-91f1-1ddbc34d36b6)

This device uses a ESP32 to interogate the status of [hasanukegoneoff.com](https://www.hasanukegoneoff.com) and turns on either the yes or no bulb depending on the answer.

The buibs are INS-1 neon style bulbs, often found NOS and still available (as of writing) from Ukraine

# SAFETY NOTE

This board contains a 91V power supply, this has every opportunity to hurt or even kill you. If you are not trained in high voltage safety please don't build this.

# Software Setup

To setup the ESP32, load MicroPython from https://micropython.org/download/

Once loaded, put the `main.py` and `secrets.py` into the MicroPython environment.

You will need to update the `secrets.py` file with your WiFi details

# Mechanical Build

![Build_Instructions](https://github.com/user-attachments/assets/df9f7fe0-4319-4a45-a47f-980be230ea4d)

# PCB Build

The PCB is a simple double-sided board. The Gerbers are in the relevant folder and should be easy to produce by any PCB vendor.

![image](https://github.com/user-attachments/assets/908cd805-1d59-4072-98c1-91426c29486f)

![image](https://github.com/user-attachments/assets/e2d32583-fc47-4625-ab04-21b87085c7ce)


# BIST (Built in self test)

To confirm the bulbs are operating correctly and BIST function is included

The SW1 switch triggers this functionality, the illuminates both bulbs at once


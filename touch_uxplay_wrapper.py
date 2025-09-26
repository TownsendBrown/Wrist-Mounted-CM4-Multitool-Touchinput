#!/usr/bin/env python3
import os, sys, time, subprocess
from evdev import InputDevice, list_devices, ecodes

TOUCH_NAME = "10-0038 generic ft5x06 (79)"

def find_touch_device():
    """Search for the touchscreen device by name."""
    for path in list_devices():
        try:
            dev = InputDevice(path)
            if TOUCH_NAME.lower() in dev.name.lower():
                return path
        except:
            pass
    return None

def find_audio_device():
    """
    Search for the ALSA device number of 'Headphones'.
    Returns a string like 'plughw:Headphones,0' if found, else None.
    """
    result = subprocess.run(["aplay", "-L"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.lower().startswith("plughw") and "headphones" in line.lower():
            # Example line: plughw:Headphones,0
            parts = line.split(":")[1].split(",")
            card_name = parts[0]       # Headphones
            device_num = parts[1]      # 0
            print(f"Selected audio device: plughw:{card_name},{device_num}")
            return f"plughw:{card_name},{device_num}"
    return None

def main():
    print("UXPlay Touch Wrapper")
    print("Usage: sudo touch_uxplay_wrapper.py [uxplay_options]")
    print("Touch controls:")
    print("  - Single tap: Show/hide info")
    print("  - Double tap: Exit UXPlay")
    print("  - Long press (>2s): Exit UXPlay")
    print()

    # Touchscreen
    dev_path = find_touch_device()
    if not dev_path:
        print(f"Error: touchscreen '{TOUCH_NAME}' not found.")
        sys.exit(1)

    # Audio
    audio_device = find_audio_device()
    if not audio_device:
        print("Error: required audio device 'Headphones' not found.")
        sys.exit(1)

    # UXPlay launch command
    cmd = ["uxplay"]

    if len(sys.argv) > 1:
        # Use CLI arguments if provided
        cmd.extend(sys.argv[1:])
    else:
        # Default configuration (hard-coded)
        cmd.extend([
            "-bt709",
            "-s", "800x480",
            "-vs", "kmssink",
            "-as", f"alsasink device={audio_device}"
        ])

    print(f"Launching: {' '.join(cmd)}")
    print("Waiting for AirPlay connections...")

    uxplay_proc = subprocess.Popen(cmd)
    time.sleep(1)

    if uxplay_proc.poll() is not None:
        print("Failed to start UXPlay. Check if it's installed and you have permissions.")
        sys.exit(1)

    dev = InputDevice(dev_path)
    print(f"Using touchscreen: {dev.name} ({dev_path})")
    print("UXPlay is running. Use your iOS device to connect via AirPlay.")

    # Gesture state
    touching = False
    start_x = start_y = last_x = last_y = None
    start_time = None
    last_tap_time = 0
    tap_count = 0

    try:
        for event in dev.read_loop():
            if uxplay_proc.poll() is not None:
                print("UXPlay process ended.")
                break

            if event.type == ecodes.EV_ABS:
                if event.code in (ecodes.ABS_MT_POSITION_X, ecodes.ABS_X):
                    last_x = event.value
                elif event.code in (ecodes.ABS_MT_POSITION_Y, ecodes.ABS_Y):
                    last_y = event.value

            elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                if event.value == 1:  # finger down
                    touching = True
                    start_time = time.time()
                    start_x, start_y = last_x, last_y

                elif event.value == 0 and touching:  # finger up
                    touching = False
                    current_time = time.time()
                    duration = current_time - start_time

                    if start_x is None or last_x is None:
                        continue

                    dx = last_x - start_x
                    dy = last_y - start_y

                    # Gesture detection
                    if abs(dx) < 30 and abs(dy) < 30 and duration < 0.3:
                        if current_time - last_tap_time < 0.6:
                            tap_count += 1
                            if tap_count >= 2:
                                print("Double tap detected - Exiting UXPlay...")
                                break
                        else:
                            tap_count = 1
                            print("Single tap detected")
                        last_tap_time = current_time

                    elif duration > 2.0 and abs(dx) < 20 and abs(dy) < 20:
                        print("Long press detected - Exiting UXPlay...")
                        break

                    if current_time - last_tap_time > 1.0:
                        tap_count = 0

    except KeyboardInterrupt:
        print("\nKeyboard interrupt received.")
    finally:
        print("Shutting down UXPlay...")
        try:
            uxplay_proc.terminate()
            try:
                uxplay_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                print("Force killing UXPlay...")
                uxplay_proc.kill()
                uxplay_proc.wait()
        except Exception as e:
            print(f"Error shutting down UXPlay: {e}")
        print("UXPlay stopped.")

if __name__ == "__main__":
    main()

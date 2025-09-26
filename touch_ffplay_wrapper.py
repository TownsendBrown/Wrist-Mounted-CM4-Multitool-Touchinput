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

def check_video_device(device_path="/dev/video0"):
    """Check if the video device exists and is accessible."""
    if not os.path.exists(device_path):
        return False, f"Video device {device_path} does not exist"
    
    try:
        # Try to access the device
        with open(device_path, 'rb') as f:
            pass
        return True, f"Video device {device_path} is accessible"
    except PermissionError:
        return False, f"Permission denied accessing {device_path}. Try running with sudo."
    except Exception as e:
        return False, f"Error accessing {device_path}: {str(e)}"

def main():
    print("FFplay Touch Wrapper")
    print("Usage: sudo touch_ffplay_wrapper.py [video_device]")
    print("Touch controls:")
    print("  - 4 consecutive taps: Exit FFplay")
    print("  - Long press (>2s): Exit FFplay")
    print()

    # Check for video device argument
    video_device = sys.argv[1] if len(sys.argv) > 1 else "/dev/video0"
    
    # Check video device
    device_ok, device_msg = check_video_device(video_device)
    print(device_msg)
    if not device_ok:
        print("Available video devices:")
        for i in range(10):  # Check video0 through video9
            test_device = f"/dev/video{i}"
            if os.path.exists(test_device):
                print(f"  - {test_device}")
        sys.exit(1)

    # Touchscreen
    dev_path = find_touch_device()
    if not dev_path:
        print(f"Error: touchscreen '{TOUCH_NAME}' not found.")
        print("Available input devices:")
        for path in list_devices():
            try:
                dev = InputDevice(path)
                print(f"  - {dev.name} ({path})")
            except:
                pass
        sys.exit(1)

    # FFplay launch command
    cmd = [
        "ffplay",
        "-f", "v4l2",
        "-i", video_device,
        "-vf", "scale=640:480",
        "-an",  # no audio
        "-sn"   # no subtitles
    ]

    print(f"Launching: {' '.join(cmd)}")
    print("Starting camera preview...")

    ffplay_proc = subprocess.Popen(cmd)
    time.sleep(1)

    if ffplay_proc.poll() is not None:
        print("Failed to start FFplay. Check if it's installed and the video device is working.")
        print("Install ffmpeg with: sudo apt-get install ffmpeg")
        sys.exit(1)

    dev = InputDevice(dev_path)
    print(f"Using touchscreen: {dev.name} ({dev_path})")
    print("Camera preview is running. Use touch controls to exit.")

    # Gesture state
    touching = False
    start_x = start_y = last_x = last_y = None
    start_time = None
    last_tap_time = 0
    consecutive_taps = 0
    tap_timeout = 1.0  # Reset tap count after 1 second of no taps

    try:
        for event in dev.read_loop():
            if ffplay_proc.poll() is not None:
                print("FFplay process ended.")
                break

            current_time = time.time()
            
            # Reset tap count if too much time has passed
            if current_time - last_tap_time > tap_timeout:
                consecutive_taps = 0

            if event.type == ecodes.EV_ABS:
                if event.code in (ecodes.ABS_MT_POSITION_X, ecodes.ABS_X):
                    last_x = event.value
                elif event.code in (ecodes.ABS_MT_POSITION_Y, ecodes.ABS_Y):
                    last_y = event.value

            elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                if event.value == 1:  # finger down
                    touching = True
                    start_time = current_time
                    start_x, start_y = last_x, last_y

                elif event.value == 0 and touching:  # finger up
                    touching = False
                    duration = current_time - start_time

                    if start_x is None or last_x is None:
                        continue

                    dx = abs(last_x - start_x) if last_x is not None and start_x is not None else 0
                    dy = abs(last_y - start_y) if last_y is not None and start_y is not None else 0

                    # Gesture detection
                    if dx < 30 and dy < 30 and duration < 0.3:  # Quick tap in place
                        consecutive_taps += 1
                        last_tap_time = current_time
                        print(f"Tap {consecutive_taps}/4")
                        
                        if consecutive_taps >= 4:
                            print("4 taps detected - Exiting FFplay...")
                            break

                    elif duration > 2.0 and dx < 20 and dy < 20:  # Long press
                        print("Long press detected - Exiting FFplay...")
                        break
                    else:
                        # Movement or longer tap - reset counter
                        consecutive_taps = 0

    except KeyboardInterrupt:
        print("\nKeyboard interrupt received.")
    finally:
        print("Shutting down FFplay...")
        try:
            ffplay_proc.terminate()
            try:
                ffplay_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                print("Force killing FFplay...")
                ffplay_proc.kill()
                ffplay_proc.wait()
        except Exception as e:
            print(f"Error shutting down FFplay: {e}")
        print("FFplay stopped.")

if __name__ == "__main__":
    main()

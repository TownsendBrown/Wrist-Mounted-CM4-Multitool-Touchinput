#!/usr/bin/env python3
import os, sys, time, json, socket, subprocess
from evdev import InputDevice, list_devices, ecodes

MPV_SOCKET = "/tmp/mpvsocket"
TOUCH_NAME = "10-0038 generic ft5x06 (79)"
AUDIO_NAME = "bcm2835 Headphones"

def find_touch_device():
    for path in list_devices():
        try:
            dev = InputDevice(path)
            if TOUCH_NAME.lower() in dev.name.lower():
                return path
        except:
            pass
    return None

def get_display_size():
    try:
        with open("/sys/class/graphics/fb0/virtual_size") as f:
            w, h = f.read().strip().split(",")
            return int(w), int(h)
    except:
        return 800, 480  # fallback

def find_audio_device(target_name=AUDIO_NAME):
    """Look up ALSA PCM name for bcm2835 Headphones using aplay -L."""
    result = subprocess.run(["aplay", "-L"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if "sysdefault:CARD=Headphones" in line:
            print(f"Selected audio device: {line.strip()} ({target_name})")
            return line.strip()
    print("No bcm2835 Headphones device found, using default audio.")
    return None

def mpv_send(cmd):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)  # Add timeout to prevent hanging
        sock.connect(MPV_SOCKET)
        sock.send((json.dumps({"command": cmd}) + "\n").encode("utf-8"))
        sock.close()
        return True
    except Exception as e:
        print(f"IPC error: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage: sudo touch_mpv_wrapper.py <video.mp4>")
        sys.exit(1)

    video = sys.argv[1]

    # Touchscreen
    dev_path = find_touch_device()
    if not dev_path:
        print(f"Touchscreen '{TOUCH_NAME}' not found.")
        sys.exit(1)

    # Display
    width, height = get_display_size()
    print(f"Display size: {width}x{height}")

    # Audio
    audio_device = find_audio_device()

    # Cleanup old socket
    if os.path.exists(MPV_SOCKET):
        os.remove(MPV_SOCKET)

    # MPV launch
    cmd = ["mpv", "--quiet", "--vo=drm",
           f"--input-ipc-server={MPV_SOCKET}", video]
    if audio_device:
        cmd.append(f"--audio-device=alsa/{audio_device}")

    mpv_proc = subprocess.Popen(cmd)
    time.sleep(0.3)  # let mpv start

    dev = InputDevice(dev_path)
    print(f"Using touchscreen: {dev.name} ({dev_path})")

    # Gesture state
    touching = False
    start_x = start_y = last_x = last_y = None
    start_time = None
    last_volume_time = 0  # For debouncing volume commands

    try:
        for event in dev.read_loop():
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
                    duration = time.time() - start_time
                    if start_x is None or last_x is None:
                        continue

                    dx = last_x - start_x
                    dy = last_y - start_y

                    if abs(dx) < 30 and abs(dy) < 30 and duration < 0.3:
                        # Tap → pause/play
                        mpv_send(["cycle", "pause"])

                    elif duration > 1.0 and abs(dx) < 20 and abs(dy) < 20:
                        # Long press → quit
                        mpv_send(["quit"])
                        break

                    elif abs(dx) > abs(dy):
                        # Horizontal drag → seek
                        seek = int(dx / width * 120)  # full-width drag = ±120s
                        if seek != 0:
                            mpv_send(["seek", seek, "relative"])

                    else:
                        # Vertical drag → volume (FIXED LOGIC)
                        current_time = time.time()
                        
                        # Debounce: Only allow volume changes every 100ms
                        if current_time - last_volume_time < 0.1:
                            continue
                            
                        # Minimum swipe threshold (pixels)
                        min_swipe = 20
                        if abs(dy) < min_swipe:
                            continue
                            
                        # Fixed volume step regardless of swipe distance
                        volume_step = 5  # Always ±5% volume
                        
                        if dy < -min_swipe:  # Swipe up = volume up
                            vol_change = volume_step
                        elif dy > min_swipe:   # Swipe down = volume down  
                            vol_change = -volume_step
                        else:
                            continue
                            
                        # Send volume command with error handling
                        if mpv_send(["add", "volume", vol_change]):
                            last_volume_time = current_time
                            print(f"Volume: {vol_change:+d}%")
                        else:
                            print("Failed to send volume command")
                            
    finally:
        mpv_proc.terminate()
        mpv_proc.wait()

if __name__ == "__main__":
    main()

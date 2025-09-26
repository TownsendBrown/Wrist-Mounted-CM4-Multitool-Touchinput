#!/usr/bin/env python3

import os
import sys
import time
import struct
import termios
import tty
import select
import threading
import math
import subprocess
import glob
from collections import deque
from datetime import datetime
from pathlib import Path
from evdev import InputDevice, categorize, ecodes, list_devices

class FramebufferInfo:
    """Get framebuffer information"""
    
    def __init__(self, device='/dev/fb0'):
        self.device = device
        # Fixed resolution for Waveshare 4.3" display
        self.width = 800
        self.height = 480

class TouchHandler:
    """Handles touch input from FT5x06 controller"""
    
    def __init__(self, device_name="ft5x06"):
        self.device = None
        self.touch_callback = None
        self.running = False
        self.touch_thread = None
        self.current_touch = None
        self.last_touch_time = 0
        self.last_tap_time = 0
        self.tap_count = 0
        self.fb_info = FramebufferInfo()
        
        # Find the touch device - try multiple possible names
        possible_names = ["ft5x06", "10-0038", "generic ft5x06", "edt-ft5x06"]
        devices = [InputDevice(path) for path in list_devices()]
        
        for device in devices:
            device_lower = device.name.lower()
            for name in possible_names:
                if name in device_lower:
                    self.device = device
                    print(f"Found touch device: {device.name}")
                    break
            if self.device:
                break
        
        if not self.device:
            print(f"Warning: Touch device not found. Tried: {possible_names}")
            print("Available devices:")
            for device in devices:
                print(f"  - {device.name}")
    
    def start(self, callback):
        """Start listening for touch events"""
        if not self.device:
            print("Touch device not available, touch input disabled")
            return
            
        self.touch_callback = callback
        self.running = True
        self.touch_thread = threading.Thread(target=self._touch_loop)
        self.touch_thread.daemon = True
        self.touch_thread.start()
    
    def stop(self):
        """Stop listening for touch events"""
        self.running = False
        if self.touch_thread:
            self.touch_thread.join()
    
    def _touch_loop(self):
        """Main touch event loop"""
        x, y = None, None
        touching = False
        swipe_start = None
        
        # Fixed max coordinates for 800x480 display
        max_x = 800
        max_y = 480
        
        for event in self.device.read_loop():
            if not self.running:
                break
            
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_MT_POSITION_X or event.code == ecodes.ABS_X:
                    x = event.value
                elif event.code == ecodes.ABS_MT_POSITION_Y or event.code == ecodes.ABS_Y:
                    y = event.value
                elif event.code == ecodes.ABS_MT_TRACKING_ID:
                    if event.value >= 0:
                        touching = True
                        if x is not None and y is not None:
                            swipe_start = (x, y, time.time())
                    else:
                        touching = False
                        if self.touch_callback and x is not None and y is not None:
                            # Check for swipe
                            if swipe_start:
                                dx = x - swipe_start[0]
                                dy = y - swipe_start[1]
                                dt = time.time() - swipe_start[2]
                                
                                # Check for double tap
                                current_time = time.time()
                                if dt < 0.2:  # Quick tap
                                    if current_time - self.last_tap_time < 0.5:
                                        self.tap_count += 1
                                        if self.tap_count >= 2:
                                            self.touch_callback('double_tap', x, y)
                                            self.tap_count = 0
                                    else:
                                        self.tap_count = 1
                                    self.last_tap_time = current_time
                                    
                                    if abs(dx) > max_x * 0.1 or abs(dy) > max_y * 0.1:
                                        if abs(dx) > abs(dy):
                                            gesture = 'swipe_right' if dx > 0 else 'swipe_left'
                                        else:
                                            gesture = 'swipe_down' if dy > 0 else 'swipe_up'
                                        self.touch_callback(gesture, x, y)
                                    else:
                                        self.touch_callback('tap', x, y)
                                else:
                                    self.touch_callback('release', x, y)
                            swipe_start = None
            
            elif event.type == ecodes.EV_KEY:
                if event.code == ecodes.BTN_TOUCH:
                    if event.value == 1:
                        touching = True
                        if x is not None and y is not None:
                            swipe_start = (x, y, time.time())
                            if self.touch_callback:
                                self.touch_callback('press', x, y)
                    else:
                        touching = False
                        if self.touch_callback and x is not None and y is not None:
                            self.touch_callback('release', x, y)
            
            elif event.type == ecodes.EV_SYN:
                if touching and x is not None and y is not None:
                    self.current_touch = (x, y)

class ASCIICanvas:
    """Main ASCII canvas for rendering UI with double buffering"""
    
    def __init__(self, width=None, height=None):
        # Auto-detect terminal size if not specified
        if width is None or height is None:
            try:
                rows, cols = os.popen('stty size', 'r').read().split()
                self.width = width or int(cols)
                self.height = height or int(rows)
            except:
                self.width = width or 80
                self.height = height or 24
        else:
            self.width = width
            self.height = height
            
        self.buffer = [[' ' for _ in range(self.width)] for _ in range(self.height)]
        self.old_buffer = [[' ' for _ in range(self.width)] for _ in range(self.height)]
        self.dirty = True  # Track if redraw is needed
        
        # Setup console for better rendering
        self._setup_console()
    
    def _setup_console(self):
        """Setup console for optimal rendering"""
        # Save cursor position
        print('\033[s', end='')
        # Hide cursor
        print('\033[?25l', end='')
        # Clear screen
        print('\033[2J\033[H', end='')
        # Disable line wrap
        print('\033[?7l', end='')
        # Set UTF-8 if needed
        if sys.stdout.encoding != 'utf-8':
            import codecs
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    
    def cleanup_console(self):
        """Restore console settings"""
        # Show cursor
        print('\033[?25h', end='')
        # Enable line wrap
        print('\033[?7h', end='')
        # Clear screen
        print('\033[2J\033[H', end='')
        # Restore cursor position
        print('\033[u', end='')
        sys.stdout.flush()
    
    def clear(self):
        """Clear the canvas"""
        for y in range(self.height):
            for x in range(self.width):
                self.buffer[y][x] = ' '
        self.dirty = True
    
    def draw_text(self, x, y, text, clip=True):
        """Draw text at position"""
        if y < 0 or y >= self.height:
            return
        
        for i, char in enumerate(text):
            px = x + i
            if px < 0:
                continue
            if px >= self.width:
                if clip:
                    break
                else:
                    continue
            self.buffer[y][px] = char
        self.dirty = True
    
    def draw_box(self, x, y, w, h, style='single', filled=False):
        """Draw a box with various styles"""
        if style == 'single':
            chars = '┌─┐│└┘'
        elif style == 'double':
            chars = '╔═╗║╚╝'
        elif style == 'round':
            chars = '╭─╮│╰╯'
        else:
            chars = '+-+|++'
        
        # Fill background if requested
        if filled:
            for row in range(max(0, y), min(y+h, self.height)):
                for col in range(max(0, x), min(x+w, self.width)):
                    self.buffer[row][col] = '░'
        
        # Top line
        if 0 <= y < self.height:
            if x >= 0 and x < self.width:
                self.buffer[y][x] = chars[0]
            for i in range(1, w-1):
                if 0 <= x+i < self.width:
                    self.buffer[y][x+i] = chars[1]
            if 0 <= x+w-1 < self.width:
                self.buffer[y][x+w-1] = chars[2]
        
        # Sides
        for i in range(1, h-1):
            if 0 <= y+i < self.height:
                if 0 <= x < self.width:
                    self.buffer[y+i][x] = chars[3]
                if 0 <= x+w-1 < self.width:
                    self.buffer[y+i][x+w-1] = chars[3]
        
        # Bottom line
        if 0 <= y+h-1 < self.height:
            if 0 <= x < self.width:
                self.buffer[y+h-1][x] = chars[4]
            for i in range(1, w-1):
                if 0 <= x+i < self.width:
                    self.buffer[y+h-1][x+i] = chars[1]
            if 0 <= x+w-1 < self.width:
                self.buffer[y+h-1][x+w-1] = chars[5]
        
        self.dirty = True
    
    def render(self):
        """Render the canvas to terminal with optimized updates"""
        if not self.dirty:
            return
        
        # Build output string all at once to minimize flicker
        output = []
        
        # Position cursor at home
        output.append('\033[H')
        
        # Render each line
        for y in range(self.height):
            # Move to beginning of line
            output.append(f'\033[{y+1};1H')
            # Clear line first to prevent ghosting
            output.append('\033[K')
            # Draw the line content
            line = ''.join(self.buffer[y])
            output.append(line)
        
        # Write everything at once
        sys.stdout.write(''.join(output))
        sys.stdout.flush()
        
        # Copy buffer to old buffer
        for y in range(self.height):
            self.old_buffer[y] = self.buffer[y][:]
        
        self.dirty = False

class LoadingScreen:
    """ASCII art loading screen with animation"""
    
    def __init__(self, canvas):
        self.canvas = canvas
        self.frame = 0
        self.done = False
        
        # ASCII art logo frames (customizable)
        self.logo_frames = [
            [
                "    ╔═══════════════════╗    ",
                "    ║  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ║    ",
                "    ║  ███████████████  ║    ",
                "    ║  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ║    ",
                "    ╚═══════════════════╝    ",
                "         LOADING...          "
            ],
            [
                "    ╔═══════════════════╗    ",
                "    ║  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ║    ",
                "    ║  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ║    ",
                "    ║  ███████████████  ║    ",
                "    ╚═══════════════════╝    ",
                "         LOADING...          "
            ],
            [
                "    ╔═══════════════════╗    ",
                "    ║  ███████████████  ║    ",
                "    ║  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ║    ",
                "    ║  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ║    ",
                "    ╚═══════════════════╝    ",
                "         LOADING...          "
            ]
        ]
        
        # Loading bar animation
        self.progress = 0
        self.spinner_frames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        self.spinner_index = 0
    
    def update(self):
        """Update loading animation"""
        self.canvas.clear()
        
        # Calculate center position
        logo = self.logo_frames[self.frame % len(self.logo_frames)]
        start_y = (self.canvas.height - len(logo)) // 2 - 2
        
        # Draw logo
        for i, line in enumerate(logo):
            x = (self.canvas.width - len(line)) // 2
            self.canvas.draw_text(x, start_y + i, line)
        
        # Draw progress bar
        bar_width = 40
        bar_x = (self.canvas.width - bar_width - 2) // 2
        bar_y = start_y + len(logo) + 2
        
        filled = int(bar_width * self.progress)
        bar = '█' * filled + '░' * (bar_width - filled)
        self.canvas.draw_text(bar_x, bar_y, f'[{bar}]')
        
        # Draw percentage
        percent_text = f"{int(self.progress * 100)}%"
        self.canvas.draw_text((self.canvas.width - len(percent_text)) // 2, bar_y + 1, percent_text)
        
        # Draw spinner
        spinner_text = f"Initializing {self.spinner_frames[self.spinner_index]}"
        self.canvas.draw_text((self.canvas.width - len(spinner_text)) // 2, bar_y + 3, spinner_text)
        
        # Update animation states
        self.frame += 1
        self.spinner_index = (self.spinner_index + 1) % len(self.spinner_frames)
        self.progress += 0.02
        
        if self.progress >= 1.0:
            self.done = True
        
        self.canvas.render()
        return not self.done

class Widget:
    """Base widget class"""
    
    def __init__(self, x, y, width, height):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.focused = False
        self.visible = True
        self.fb_info = FramebufferInfo()
    
    def draw(self, canvas):
        """Override in subclasses"""
        pass
    
    def handle_touch(self, event_type, x, y):
        """Handle touch events"""
        pass
    
    def contains_point(self, x, y):
        """Check if point is within widget bounds"""
        # Convert touch coordinates to terminal coordinates
        # Fixed mapping for 800x480 display
        term_cols, term_rows = self._get_terminal_size()
        term_x = int(x * term_cols / 800)
        term_y = int(y * term_rows / 480)
        
        return (self.x <= term_x < self.x + self.width and
                self.y <= term_y < self.y + self.height)
    
    def _get_terminal_size(self):
        """Get terminal size"""
        try:
            rows, cols = os.popen('stty size', 'r').read().split()
            return int(cols), int(rows)
        except:
            return 80, 24

class Button(Widget):
    """ASCII button widget"""
    
    def __init__(self, x, y, text, callback=None, style='single'):
        self.text = text
        self.callback = callback
        self.pressed = False
        self.style = style
        super().__init__(x, y, len(text) + 4, 3)
    
    def draw(self, canvas):
        if not self.visible:
            return
        
        box_style = 'double' if self.pressed else self.style
        canvas.draw_box(self.x, self.y, self.width, self.height, box_style, self.pressed)
        
        # Center text
        text_x = self.x + (self.width - len(self.text)) // 2
        canvas.draw_text(text_x, self.y + 1, self.text)
    
    def handle_touch(self, event_type, x, y):
        if event_type == 'press':
            self.pressed = True
            return True
        elif event_type in ['release', 'tap']:
            was_pressed = self.pressed
            self.pressed = False  # Always reset pressed state
            if was_pressed and self.callback:
                self.callback()
            return True
        return False

class VideoButton(Widget):
    """Button for video selection"""
    
    def __init__(self, x, y, width, filename, filepath, callback):
        self.filename = filename[:width-4] if len(filename) > width-4 else filename  # Truncate if too long
        self.filepath = filepath
        self.callback = callback
        self.pressed = False
        super().__init__(x, y, width, 3)
    
    def draw(self, canvas):
        if not self.visible:
            return
        
        # Draw box
        style = 'double' if self.pressed else 'single'
        canvas.draw_box(self.x, self.y, self.width, self.height, style, self.pressed)
        
        # Draw filename with video icon
        icon = "▶ "
        max_text_len = self.width - 4 - len(icon)
        display_text = icon + self.filename[:max_text_len]
        text_x = self.x + 2
        canvas.draw_text(text_x, self.y + 1, display_text)
    
    def handle_touch(self, event_type, x, y):
        if event_type == 'press':
            self.pressed = True
            return True
        elif event_type in ['release', 'tap']:
            was_pressed = self.pressed
            self.pressed = False  # Always reset pressed state
            if was_pressed and self.callback:
                self.callback(self.filepath)
            return True
        return False

class ScrollBar(Widget):
    """Vertical scrollbar widget"""
    
    def __init__(self, x, y, height, total_items, visible_items, callback=None):
        self.total_items = max(1, total_items)
        self.visible_items = min(visible_items, self.total_items)
        self.scroll_position = 0
        self.callback = callback
        self.dragging = False
        super().__init__(x, y, 3, height)
    
    def draw(self, canvas):
        if not self.visible or self.total_items <= self.visible_items:
            return
        
        # Draw scrollbar track
        for i in range(self.height):
            canvas.draw_text(self.x, self.y + i, '│')
        
        # Calculate thumb size and position
        thumb_height = max(1, int(self.height * self.visible_items / self.total_items))
        max_scroll = self.total_items - self.visible_items
        if max_scroll > 0:
            thumb_pos = int((self.height - thumb_height) * self.scroll_position / max_scroll)
        else:
            thumb_pos = 0
        
        # Draw thumb
        for i in range(thumb_height):
            if 0 <= self.y + thumb_pos + i < self.y + self.height:
                canvas.draw_text(self.x, self.y + thumb_pos + i, '█')
    
    def handle_touch(self, event_type, x, y):
        if event_type in ['press', 'tap']:
            self.dragging = True
            self._update_position(y)
            return True
        elif event_type == 'release':
            self.dragging = False
            return True
        return False
    
    def _update_position(self, touch_y):
        """Update scroll position based on touch"""
        term_cols, term_rows = self._get_terminal_size()
        term_y = int(touch_y * term_rows / 480)
        
        # Calculate relative position
        relative_y = term_y - self.y
        normalized = max(0, min(1, relative_y / self.height))
        
        # Update scroll position
        max_scroll = self.total_items - self.visible_items
        self.scroll_position = int(normalized * max_scroll)
        
        if self.callback:
            self.callback(self.scroll_position)
    
    def set_position(self, position):
        """Set scroll position programmatically"""
        max_scroll = self.total_items - self.visible_items
        self.scroll_position = max(0, min(position, max_scroll))

class MainMenuButton(Widget):
    """Large button for main menu"""
    
    def __init__(self, x, y, text, icon, callback=None):
        self.text = text
        self.icon = icon
        self.callback = callback
        self.pressed = False
        super().__init__(x, y, 18, 7)
    
    def draw(self, canvas):
        if not self.visible:
            return
        
        style = 'double' if self.pressed else 'round'
        canvas.draw_box(self.x, self.y, self.width, self.height, style, self.pressed)
        
        # Draw icon
        icon_lines = self.icon.split('\n')
        for i, line in enumerate(icon_lines):
            x_pos = self.x + (self.width - len(line)) // 2
            canvas.draw_text(x_pos, self.y + 1 + i, line)
        
        # Draw text
        text_x = self.x + (self.width - len(self.text)) // 2
        canvas.draw_text(text_x, self.y + self.height - 2, self.text)
    
    def handle_touch(self, event_type, x, y):
        if event_type == 'press':
            self.pressed = True
            return True
        elif event_type in ['release', 'tap']:
            was_pressed = self.pressed
            self.pressed = False  # Always reset pressed state
            if was_pressed and self.callback:
                # Add a small delay so the button release is visible
                self.callback()
            return True
        return False

class Slider(Widget):
    """Slider widget for settings"""
    
    def __init__(self, x, y, width, min_val=0, max_val=100, value=50, callback=None):
        self.min_val = min_val
        self.max_val = max_val
        self.value = value
        self.callback = callback
        self.dragging = False
        super().__init__(x, y, width, 3)
    
    def draw(self, canvas):
        if not self.visible:
            return
        
        # Draw slider track
        canvas.draw_text(self.x, self.y + 1, '├' + '─' * (self.width - 2) + '┤')
        
        # Calculate handle position
        normalized = (self.value - self.min_val) / (self.max_val - self.min_val)
        handle_x = self.x + 1 + int((self.width - 3) * normalized)
        
        # Draw handle
        canvas.draw_text(handle_x, self.y + 1, '●')
        
        # Draw value
        value_text = f"{self.value}%"
        canvas.draw_text(self.x + (self.width - len(value_text)) // 2, self.y, value_text)
    
    def handle_touch(self, event_type, x, y):
        if event_type in ['press', 'tap']:
            self.dragging = True
            self._update_value(x)
            return True
        elif event_type == 'release':
            self.dragging = False
            return True
        return False
    
    def _update_value(self, touch_x):
        """Update slider value based on touch position"""
        term_cols, _ = self._get_terminal_size()
        term_x = int(touch_x * term_cols / 800)
        
        # Calculate new value
        relative_x = term_x - self.x - 1
        normalized = max(0, min(1, relative_x / (self.width - 3)))
        self.value = int(self.min_val + normalized * (self.max_val - self.min_val))
        
        if self.callback:
            self.callback(self.value)

class Screen:
    """Base screen class"""
    
    def __init__(self, app):
        self.app = app
        self.widgets = []
        self.title = ""
    
    def enter(self):
        """Called when entering this screen"""
        pass
    
    def exit(self):
        """Called when leaving this screen"""
        pass
    
    def handle_touch(self, event_type, x, y):
        """Handle touch events"""
        for widget in reversed(self.widgets):
            if widget.contains_point(x, y):
                if widget.handle_touch(event_type, x, y):
                    return True
        return False
    
    def draw(self, canvas):
        """Draw the screen"""
        canvas.clear()
        
        # Draw title bar
        if self.title:
            title_text = f"╔══ {self.title} ══╗"
            canvas.draw_text((canvas.width - len(title_text)) // 2, 1, title_text)
            canvas.draw_text(0, 2, '═' * canvas.width)
        
        # Draw widgets
        for widget in self.widgets:
            widget.draw(canvas)

class MainMenuScreen(Screen):
    """Main menu screen"""
    
    def __init__(self, app):
        super().__init__(app)
        self.title = "MAIN MENU"
        
        # Get terminal size for proper positioning
        try:
            rows, cols = os.popen('stty size', 'r').read().split()
            term_width = int(cols)
            term_height = int(rows)
        except:
            term_width = 80
            term_height = 24
        
        # Define menu items with icons
        menu_items = [
            ("Settings", "  ⚙  \n ───", self.open_settings),
            ("Apps", " ▣▣▣ \n ▣▣▣", self.open_apps),
            ("Monitor", " ▂▄▆ \n ───", self.open_monitor),
            ("Testing", " ✓✗ \n ───", self.open_testing)
        ]
        
        # Create menu buttons in a 2x2 grid with better spacing
        button_width = 18
        button_height = 7
        spacing_x = (term_width - 2 * button_width) // 3
        spacing_y = (term_height - 2 * button_height - 8) // 3  # Leave room for title and exit button
        start_x = spacing_x
        start_y = 5
        
        for i, (text, icon, callback) in enumerate(menu_items):
            row = i // 2
            col = i % 2
            x = start_x + col * (button_width + spacing_x)
            y = start_y + row * (button_height + spacing_y)
            
            button = MainMenuButton(x, y, text, icon, callback)
            self.widgets.append(button)
        
        # Add exit button at bottom center
        exit_btn = Button((term_width - 10) // 2, term_height - 3, "Exit", self.app.quit, 'round')
        self.widgets.append(exit_btn)
    
    def open_settings(self):
        self.app.switch_screen('settings')
    
    def open_apps(self):
        self.app.switch_screen('apps')
    
    def open_monitor(self):
        self.app.switch_screen('monitor')
    
    def open_testing(self):
        self.app.switch_screen('testing')

class SettingsScreen(Screen):
    """Settings screen"""
    
    def __init__(self, app):
        super().__init__(app)
        self.title = "SETTINGS"
        
        # Get terminal size
        try:
            rows, cols = os.popen('stty size', 'r').read().split()
            term_width = int(cols)
            term_height = int(rows)
        except:
            term_width = 80
            term_height = 24
        
        # Brightness slider
        slider_width = min(40, term_width - 30)
        slider_x = (term_width - slider_width) // 2
        self.brightness_slider = Slider(slider_x, 8, slider_width, 0, 100, 50, self.update_brightness)
        self.widgets.append(self.brightness_slider)
        
        # Add label
        self.brightness_label = "Brightness"
        
        # Return button
        return_btn = Button((term_width - 15) // 2, term_height - 5, "← Main Menu", self.return_to_main, 'round')
        self.widgets.append(return_btn)
    
    def update_brightness(self, value):
        """Update display brightness"""
        try:
            # Try to set brightness via sysfs
            with open('/sys/class/backlight/rpi_backlight/brightness', 'w') as f:
                # Scale value (0-100) to actual brightness range (0-255)
                brightness = int(value * 255 / 100)
                f.write(str(brightness))
        except:
            pass  # Brightness control may not be available
    
    def return_to_main(self):
        self.app.switch_screen('main')
    
    def draw(self, canvas):
        super().draw(canvas)
        # Draw brightness label centered above slider
        label_x = (canvas.width - len(self.brightness_label)) // 2
        canvas.draw_text(label_x, 6, self.brightness_label)

class VideoPlayerScreen(Screen):
    """Video player selection screen"""
    
    def __init__(self, app):
        super().__init__(app)
        self.title = "VIDEO PLAYER"
        self.video_files = []
        self.scroll_position = 0
        self.visible_items = 10  # Number of videos visible at once
        self.content_dir = "./content"
        self.mpv_wrapper = "./touch_mpv_wrapper4.py"
        self.playing_process = None
        
        # Get terminal size
        try:
            rows, cols = os.popen('stty size', 'r').read().split()
            self.term_width = int(cols)
            self.term_height = int(rows)
        except:
            self.term_width = 80
            self.term_height = 24
        
        # Calculate list dimensions
        self.list_width = min(60, self.term_width - 10)
        self.list_x = (self.term_width - self.list_width) // 2
        self.list_y = 5
        
        # Setup widgets
        self.setup_widgets()
        
        # Load video files
        self.load_videos()
    
    def setup_widgets(self):
        """Setup the screen widgets"""
        self.widgets = []
        
        # Return button
        return_btn = Button((self.term_width - 15) // 2, self.term_height - 3, "← Apps Menu", self.return_to_apps, 'round')
        self.widgets.append(return_btn)
        
        # Scrollbar
        self.scrollbar = ScrollBar(
            self.list_x + self.list_width + 2,
            self.list_y,
            min(self.visible_items * 3, self.term_height - 10),
            0,  # Will be updated when videos load
            self.visible_items,
            self.on_scroll
        )
        self.widgets.append(self.scrollbar)
    
    def load_videos(self):
        """Load video files from content directory"""
        # Create content directory if it doesn't exist
        if not os.path.exists(self.content_dir):
            os.makedirs(self.content_dir)
            print(f"Created content directory: {self.content_dir}")
        
        # Clear existing video buttons
        self.widgets = [w for w in self.widgets if not isinstance(w, VideoButton)]
        
        # Find video files
        self.video_files = []
        extensions = ['*.mp4', '*.mkv', '*.avi']
        
        for ext in extensions:
            pattern = os.path.join(self.content_dir, ext)
            files = glob.glob(pattern)
            self.video_files.extend(files)
        
        # Sort alphabetically
        self.video_files.sort()
        
        # Update scrollbar
        self.scrollbar.total_items = len(self.video_files)
        self.scrollbar.set_position(0)
        self.scroll_position = 0
        
        # Create video buttons
        self.update_video_list()
    
    def update_video_list(self):
        """Update the visible video buttons based on scroll position"""
        # Remove old video buttons
        self.widgets = [w for w in self.widgets if not isinstance(w, VideoButton)]
        
        if not self.video_files:
            return
        
        # Calculate visible range
        start_idx = self.scroll_position
        end_idx = min(start_idx + self.visible_items, len(self.video_files))
        
        # Create buttons for visible videos
        for i, video_path in enumerate(self.video_files[start_idx:end_idx]):
            filename = os.path.basename(video_path)
            button = VideoButton(
                self.list_x,
                self.list_y + i * 3,
                self.list_width,
                filename,
                video_path,
                self.play_video
            )
            self.widgets.append(button)
    
    def on_scroll(self, position):
        """Handle scrollbar position changes"""
        self.scroll_position = position
        self.update_video_list()
        self.app.screen_dirty = True
    
    def play_video(self, video_path):
        """Launch video player with selected video"""
        # Check if wrapper script exists
        if not os.path.exists(self.mpv_wrapper):
            self.show_error("Player not found")
            return
        
        # Clear screen
        os.system('clear')
        
        try:
            # Launch the video player
            print(f"Launching video: {os.path.basename(video_path)}")
            print("Press and hold to exit video...")
            
            # Run the mpv wrapper with sudo
            cmd = ['sudo', 'python3', self.mpv_wrapper, video_path]
            self.playing_process = subprocess.run(cmd)
            
            # Video finished, return to menu
            os.system('clear')
            self.app.screen_dirty = True
            
        except Exception as e:
            self.show_error(f"Failed to launch: {str(e)}")
            os.system('clear')
            self.app.screen_dirty = True
    
    def show_error(self, message):
        """Display error message briefly"""
        os.system('clear')
        print(f"\nError: {message}")
        print("\nPress any key to continue...")
        time.sleep(2)
        os.system('clear')
        self.app.screen_dirty = True
    
    def return_to_apps(self):
        """Return to apps menu"""
        self.app.switch_screen('apps')
    
    def handle_touch(self, event_type, x, y):
        """Handle touch events including swipe for scrolling"""
        # Handle swipe gestures for scrolling
        if event_type == 'swipe_up' and self.scroll_position > 0:
            self.scroll_position = max(0, self.scroll_position - 1)
            self.scrollbar.set_position(self.scroll_position)
            self.update_video_list()
            self.app.screen_dirty = True
            return True
        elif event_type == 'swipe_down' and self.scroll_position < len(self.video_files) - self.visible_items:
            self.scroll_position = min(len(self.video_files) - self.visible_items, self.scroll_position + 1)
            self.scrollbar.set_position(self.scroll_position)
            self.update_video_list()
            self.app.screen_dirty = True
            return True
        
        # Handle widget touches
        return super().handle_touch(event_type, x, y)
    
    def draw(self, canvas):
        super().draw(canvas)
        
        if not self.video_files:
            # Show "no videos found" message
            msg = "No videos found"
            canvas.draw_text((canvas.width - len(msg)) // 2, canvas.height // 2, msg)
            
            # Show content directory path
            path_msg = f"Place .mp4, .mkv, or .avi files in: {self.content_dir}"
            canvas.draw_text((canvas.width - len(path_msg)) // 2, canvas.height // 2 + 2, path_msg)
        else:
            # Show video count
            count_msg = f"Videos: {len(self.video_files)}"
            canvas.draw_text(self.list_x, self.list_y - 2, count_msg)
            
            # Show scroll hint if needed
            if len(self.video_files) > self.visible_items:
                hint = "↑↓ Swipe to scroll"
                canvas.draw_text(self.list_x + self.list_width - len(hint), self.list_y - 2, hint)

class AppsScreen(Screen):
    """Apps screen with video player, AirPlay, and camera preview"""
    
    def __init__(self, app):
        super().__init__(app)
        self.title = "APPLICATIONS"
        
        # Get terminal size
        try:
            rows, cols = os.popen('stty size', 'r').read().split()
            term_width = int(cols)
            term_height = int(rows)
        except:
            term_width = 80
            term_height = 24
        
        # Create app buttons in a 3x1 grid or 2x2 grid depending on space
        button_width = 18
        button_height = 7
        
        # Try 3 buttons in a row first
        if term_width >= 3 * button_width + 40:  # Enough space for 3 buttons
            spacing_x = (term_width - 3 * button_width) // 4
            start_x = spacing_x
            center_y = (term_height - button_height) // 2 - 2
            
            # Video player button
            video_btn = MainMenuButton(
                start_x,
                center_y,
                "Video Player",
                " ▶▶▶ \n ═══",
                self.open_video_player
            )
            self.widgets.append(video_btn)
            
            # AirPlay button
            airplay_btn = MainMenuButton(
                start_x + button_width + spacing_x,
                center_y,
                "AirPlay",
                " 📱📺 \n ~~~",
                self.open_airplay
            )
            self.widgets.append(airplay_btn)
            
            # Camera button
            camera_btn = MainMenuButton(
                start_x + 2 * (button_width + spacing_x),
                center_y,
                "Camera",
                " 📷📺 \n ▲▲▲",
                self.open_camera
            )
            self.widgets.append(camera_btn)
            
        else:
            # Fall back to 2x2 grid (with 3 buttons, one row will have 2, other will have 1)
            spacing_x = (term_width - 2 * button_width) // 3
            spacing_y = (term_height - 2 * button_height - 8) // 3
            start_x = spacing_x
            start_y = 5
            
            # First row: Video Player and AirPlay
            video_btn = MainMenuButton(
                start_x,
                start_y,
                "Video Player",
                " ▶▶▶ \n ═══",
                self.open_video_player
            )
            self.widgets.append(video_btn)
            
            airplay_btn = MainMenuButton(
                start_x + button_width + spacing_x,
                start_y,
                "AirPlay",
                " 📱📺 \n ~~~",
                self.open_airplay
            )
            self.widgets.append(airplay_btn)
            
            # Second row: Camera (centered)
            camera_btn = MainMenuButton(
                (term_width - button_width) // 2,
                start_y + button_height + spacing_y,
                "Camera",
                " 📷📺 \n ▲▲▲",
                self.open_camera
            )
            self.widgets.append(camera_btn)
        
        # Return button
        return_btn = Button((term_width - 15) // 2, term_height - 5, "← Main Menu", self.return_to_main, 'round')
        self.widgets.append(return_btn)
    
    def open_video_player(self):
        """Open the video player screen"""
        self.app.switch_screen('video_player')
    
    def open_airplay(self):
        """Open the AirPlay screen"""
        self.app.switch_screen('airplay')
    
    def open_camera(self):
        """Open the Camera screen"""
        self.app.switch_screen('camera')
    
    def return_to_main(self):
        self.app.switch_screen('main')

class AirPlayScreen(Screen):
    """AirPlay screen with launch options"""
    
    def __init__(self, app):
        super().__init__(app)
        self.title = "AIRPLAY"
        self.uxplay_wrapper = "./touch_uxplay_wrapper.py"
        self.uxplay_process = None
        self.instructions = [
            "Use the wrapper's touch controls:",
            "• Single tap: Show/hide info",
            "• Double tap: Exit UXPlay",
            "• Long press (>2s): Exit UXPlay"
        ]
        
        # Get terminal size
        try:
            rows, cols = os.popen('stty size', 'r').read().split()
            term_width = int(cols)
            term_height = int(rows)
        except:
            term_width = 80
            term_height = 24
        
        # Create launch button
        launch_btn_text = "Start AirPlay"
        launch_icon = " 📡📺 \n ▲▲▲"
        launch_btn = MainMenuButton(
            (term_width - 18) // 2,
            (term_height - 7) // 2 - 3,
            launch_btn_text,
            launch_icon,
            self.launch_airplay
        )
        self.widgets.append(launch_btn)
        
        # Return button
        return_btn = Button((term_width - 15) // 2, term_height - 5, "← Apps Menu", self.return_to_apps, 'round')
        self.widgets.append(return_btn)
    
    def launch_airplay(self):
        """Launch AirPlay server"""
        # Check if wrapper script exists
        if not os.path.exists(self.uxplay_wrapper):
            self.show_error("AirPlay wrapper not found")
            return
        
        # Clear screen
        os.system('clear')
        
        try:
            # Launch the AirPlay server - the wrapper will handle touch input
            print("Starting AirPlay server...")
            print("The wrapper will take control of touch input")
            print("Use wrapper touch controls to exit")
            
            # Run the uxplay wrapper with sudo - wait for it to complete
            cmd = ['sudo', 'python3', self.uxplay_wrapper]
            self.uxplay_process = subprocess.run(cmd)
            
            # UXPlay finished, return to menu
            os.system('clear')
            self.app.screen_dirty = True
            
        except Exception as e:
            self.show_error(f"Failed to launch AirPlay: {str(e)}")
            os.system('clear')
            self.app.screen_dirty = True
    
    def show_error(self, message):
        """Display error message briefly"""
        os.system('clear')
        print(f"\nError: {message}")
        print(f"Make sure {self.uxplay_wrapper} exists in the current directory")
        print("\nPress any key to continue...")
        time.sleep(3)
        os.system('clear')
        self.app.screen_dirty = True
    
    def return_to_apps(self):
        """Return to apps menu"""
        self.app.switch_screen('apps')
    
    def exit(self):
        """Kill AirPlay process when leaving screen"""
        if self.uxplay_process and hasattr(self.uxplay_process, 'poll') and self.uxplay_process.poll() is None:
            self.uxplay_process.terminate()
            self.uxplay_process = None
            os.system('clear')
    
    def handle_touch(self, event_type, x, y):
        """Handle touch events - only process when not running UXPlay"""
        # If UXPlay is running, don't interfere with touch handling
        if self.uxplay_process and hasattr(self.uxplay_process, 'poll') and self.uxplay_process.poll() is None:
            return False
        
        # Handle widget touches when UXPlay is not running
        return super().handle_touch(event_type, x, y)
    
    def draw(self, canvas):
        """Don't draw menu when UXPlay is running"""
        # If UXPlay is running, let it take over the display
        if self.uxplay_process and hasattr(self.uxplay_process, 'poll') and self.uxplay_process.poll() is None:
            # Show instructions instead of the menu
            canvas.clear()
            for i, line in enumerate(self.instructions):
                x = (canvas.width - len(line)) // 2
                canvas.draw_text(x, canvas.height // 2 + i, line)
            canvas.render()
            return
        
        # Show normal menu when UXPlay is not running
        super().draw(canvas)
        
        # Show status information
        info_lines = [
            "AirPlay Server Control",
            "",
            "• Tap 'Start AirPlay' to begin server",
            "• Server will take over touch control",
            "• Look for this device in AirPlay settings",
            "",
            "Status: " + ("Running" if self.uxplay_process and hasattr(self.uxplay_process, 'poll') and self.uxplay_process.poll() is None else "Stopped")
        ]
        
        start_y = canvas.height // 2 + 3
        for i, line in enumerate(info_lines):
            if start_y + i < canvas.height - 3:  # Leave room for return button
                x = (canvas.width - len(line)) // 2
                canvas.draw_text(x, start_y + i, line)

class CameraScreen(Screen):
    """Camera preview screen with ffplay integration"""
    
    def __init__(self, app):
        super().__init__(app)
        self.title = "CAMERA PREVIEW"
        self.ffplay_wrapper = "./touch_ffplay_wrapper.py"
        self.ffplay_process = None
        self.video_device = "/dev/video0"
        self.instructions = [
            "Camera Preview Controls:",
            "• 4 consecutive taps: Exit preview",
            "• Long press (>2s): Exit preview"
        ]
        
        # Get terminal size
        try:
            rows, cols = os.popen('stty size', 'r').read().split()
            term_width = int(cols)
            term_height = int(rows)
        except:
            term_width = 80
            term_height = 24
        
        # Create launch button
        launch_btn_text = "Start Camera"
        launch_icon = " 📷📺 \n ▲▲▲"
        launch_btn = MainMenuButton(
            (term_width - 18) // 2,
            (term_height - 7) // 2 - 3,
            launch_btn_text,
            launch_icon,
            self.launch_camera
        )
        self.widgets.append(launch_btn)
        
        # Device selection info (you could expand this to a dropdown later)
        self.device_info = f"Device: {self.video_device}"
        
        # Return button
        return_btn = Button((term_width - 15) // 2, term_height - 5, "← Apps Menu", self.return_to_apps, 'round')
        self.widgets.append(return_btn)
    
    def launch_camera(self):
        """Launch camera preview"""
        # Check if wrapper script exists
        if not os.path.exists(self.ffplay_wrapper):
            self.show_error("Camera wrapper not found")
            return
        
        # Check if video device exists
        if not os.path.exists(self.video_device):
            self.show_error(f"Video device {self.video_device} not found")
            return
        
        # Clear screen
        os.system('clear')
        
        try:
            # Launch the camera preview - the wrapper will handle touch input
            print("Starting camera preview...")
            print("The wrapper will take control of touch input")
            print("Use 4 taps or long press to exit")
            
            # Run the ffplay wrapper with sudo - wait for it to complete
            cmd = ['sudo', 'python3', self.ffplay_wrapper, self.video_device]
            self.ffplay_process = subprocess.run(cmd)
            
            # Camera preview finished, return to menu
            os.system('clear')
            self.app.screen_dirty = True
            
        except Exception as e:
            self.show_error(f"Failed to launch camera: {str(e)}")
            os.system('clear')
            self.app.screen_dirty = True
    
    def show_error(self, message):
        """Display error message briefly"""
        os.system('clear')
        print(f"\nError: {message}")
        print(f"Make sure {self.ffplay_wrapper} exists in the current directory")
        print(f"Make sure {self.video_device} exists and is accessible")
        print("Install ffmpeg with: sudo apt-get install ffmpeg")
        print("\nReturning to menu...")
        time.sleep(3)
        os.system('clear')
        self.app.screen_dirty = True
    
    def return_to_apps(self):
        """Return to apps menu"""
        self.app.switch_screen('apps')
    
    def exit(self):
        """Kill camera process when leaving screen"""
        if self.ffplay_process and hasattr(self.ffplay_process, 'poll') and self.ffplay_process.poll() is None:
            self.ffplay_process.terminate()
            self.ffplay_process = None
            os.system('clear')
    
    def handle_touch(self, event_type, x, y):
        """Handle touch events - only process when not running camera"""
        # If camera is running, don't interfere with touch handling
        if self.ffplay_process and hasattr(self.ffplay_process, 'poll') and self.ffplay_process.poll() is None:
            return False
        
        # Handle widget touches when camera is not running
        return super().handle_touch(event_type, x, y)
    
    def draw(self, canvas):
        """Don't draw menu when camera is running"""
        # If camera is running, let it take over the display
        if self.ffplay_process and hasattr(self.ffplay_process, 'poll') and self.ffplay_process.poll() is None:
            # Show instructions instead of the menu
            canvas.clear()
            for i, line in enumerate(self.instructions):
                x = (canvas.width - len(line)) // 2
                canvas.draw_text(x, canvas.height // 2 + i, line)
            canvas.render()
            return
        
        # Show normal menu when camera is not running
        super().draw(canvas)
        
        # Show status information
        info_lines = [
            "Camera Preview Control",
            "",
            "• Tap 'Start Camera' to begin preview",
            "• Preview will take over display and touch",
            "• Use touch controls to exit preview",
            "",
            self.device_info,
            "Status: " + ("Running" if self.ffplay_process and hasattr(self.ffplay_process, 'poll') and self.ffplay_process.poll() is None else "Stopped")
        ]
        
        start_y = canvas.height // 2 + 3
        for i, line in enumerate(info_lines):
            if start_y + i < canvas.height - 3:  # Leave room for return button
                x = (canvas.width - len(line)) // 2
                canvas.draw_text(x, start_y + i, line)

class MonitorScreen(Screen):
    """Monitor screen - launches htop"""
    
    def __init__(self, app):
        super().__init__(app)
        self.title = "SYSTEM MONITOR"
        self.htop_process = None
        self.instructions = [
            "Double-tap anywhere to exit htop",
            "and return to main menu"
        ]
    
    def enter(self):
        """Launch htop when entering screen"""
        try:
            # Clear screen and show htop
            os.system('clear')
            self.htop_process = subprocess.Popen(['htop'], 
                                                stdin=sys.stdin,
                                                stdout=sys.stdout,
                                                stderr=sys.stderr)
        except FileNotFoundError:
            print("htop not found. Install with: sudo apt-get install htop")
    
    def exit(self):
        """Kill htop when leaving screen"""
        if self.htop_process:
            self.htop_process.terminate()
            self.htop_process = None
            os.system('clear')
    
    def handle_touch(self, event_type, x, y):
        """Handle double-tap to exit"""
        if event_type == 'double_tap':
            self.app.switch_screen('main')
            return True
        return False
    
    def draw(self, canvas):
        """Don't draw anything - htop takes over the display"""
        if not self.htop_process or self.htop_process.poll() is not None:
            # If htop isn't running, show instructions
            canvas.clear()
            for i, line in enumerate(self.instructions):
                x = (canvas.width - len(line)) // 2
                canvas.draw_text(x, canvas.height // 2 + i, line)
            canvas.render()

class TestingScreen(Screen):
    """Testing screen"""
    
    def __init__(self, app):
        super().__init__(app)
        self.title = "TESTING"
        
        # Get terminal size
        try:
            rows, cols = os.popen('stty size', 'r').read().split()
            term_width = int(cols)
            term_height = int(rows)
        except:
            term_width = 80
            term_height = 24
        
        # Return button
        return_btn = Button((term_width - 15) // 2, term_height - 3, "← Main Menu", self.return_to_main, 'round')
        self.widgets.append(return_btn)
        
        # Touch test display
        self.last_touch = None
        self.touch_history = deque(maxlen=5)
    
    def return_to_main(self):
        self.app.switch_screen('main')
    
    def handle_touch(self, event_type, x, y):
        """Track touch events for testing"""
        # Store touch event
        self.touch_history.append(f"{event_type}: ({x}, {y})")
        self.last_touch = (event_type, x, y)
        
        # Let parent handle widget touches
        return super().handle_touch(event_type, x, y)
    
    def draw(self, canvas):
        super().draw(canvas)
        
        # Calculate centered position for test area
        box_width = min(50, canvas.width - 20)
        box_x = (canvas.width - box_width) // 2
        
        # Draw touch test area
        canvas.draw_box(box_x, 5, box_width, 10, 'single')
        canvas.draw_text(box_x + 2, 6, "Touch Test Area")
        
        # Show last touch
        if self.last_touch:
            event, x, y = self.last_touch
            canvas.draw_text(box_x + 2, 8, f"Last: {event}")
            canvas.draw_text(box_x + 2, 9, f"Position: ({x}, {y})")
        
        # Show touch history
        canvas.draw_text(box_x + 2, 11, "History:")
        for i, event in enumerate(self.touch_history):
            if 12 + i < 15:  # Don't overflow the box
                canvas.draw_text(box_x + 2, 12 + i, f"  {event[:box_width-4]}")  # Truncate if needed

class Application:
    """Main application class"""
    
    def __init__(self):
        # Get terminal size
        try:
            rows, cols = os.popen('stty size', 'r').read().split()
            self.canvas = ASCIICanvas(int(cols), int(rows))
        except:
            self.canvas = ASCIICanvas(80, 24)
        
        self.touch_handler = TouchHandler()
        self.running = False
        self.last_render_time = 0
        self.min_render_interval = 0.033  # Cap at ~30 FPS
        
        # Initialize screens
        self.screens = {
            'main': MainMenuScreen(self),
            'settings': SettingsScreen(self),
            'apps': AppsScreen(self),
            'video_player': VideoPlayerScreen(self),
            'airplay': AirPlayScreen(self),
            'camera': CameraScreen(self),  # New camera screen
            'monitor': MonitorScreen(self),
            'testing': TestingScreen(self)
        }
        self.current_screen = None
        self.screen_dirty = True  # Track if screen needs redraw
    
    def switch_screen(self, screen_name):
        """Switch to a different screen"""
        if self.current_screen:
            self.screens[self.current_screen].exit()
        
        self.current_screen = screen_name
        self.screens[screen_name].enter()
        self.screen_dirty = True  # Force redraw on screen switch
    
    def _handle_touch(self, event_type, x, y):
        """Handle touch events"""
        if self.current_screen:
            self.screens[self.current_screen].handle_touch(event_type, x, y)
            self.screen_dirty = True  # Mark for redraw on touch
    
    def _handle_keyboard(self):
        """Handle keyboard input (non-blocking)"""
        if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
            key = sys.stdin.read(1)
            if key == 'q' or key == '\x03':  # q or Ctrl+C
                self.quit()
            elif key == '\x1b':  # ESC
                # Check for arrow keys or other escape sequences
                if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                    key2 = sys.stdin.read(1)
                    if key2 == '[':
                        if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                            key3 = sys.stdin.read(1)
                            # Process arrow keys if needed
            return True
        return False
    
    def show_loading_screen(self):
        """Display loading screen animation"""
        loading = LoadingScreen(self.canvas)
        
        while not loading.done:
            loading.update()
            time.sleep(0.05)  # Smoother animation
        
        # Clear screen after loading
        self.canvas.clear()
        self.canvas.render()
        time.sleep(0.5)
    
    def run(self):
        """Main application loop"""
        self.running = True
        
        # Show loading screen
        self.show_loading_screen()
        
        # Set terminal to raw mode for better input handling
        old_settings = termios.tcgetattr(sys.stdin)
        try:
            # Set terminal to cbreak mode instead of raw for better compatibility
            tty.setcbreak(sys.stdin.fileno())
            
            # Start touch handler
            self.touch_handler.start(self._handle_touch)
            
            # Start with main menu
            self.switch_screen('main')
            
            # Initial render
            if self.current_screen != 'monitor':
                self.screens[self.current_screen].draw(self.canvas)
                self.canvas.render()
            
            while self.running:
                current_time = time.time()
                
                # Only render if enough time has passed and screen needs update
                if current_time - self.last_render_time >= self.min_render_interval:
                    if self.current_screen != 'monitor' and self.screen_dirty:
                        # Only draw if not in htop mode and screen changed
                        self.screens[self.current_screen].draw(self.canvas)
                        self.canvas.render()
                        self.last_render_time = current_time
                        self.screen_dirty = False
                
                # Handle keyboard input
                self._handle_keyboard()
                
                # Small delay to prevent CPU spinning
                time.sleep(0.01)
                
        except KeyboardInterrupt:
            pass
        finally:
            # Restore terminal settings
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            self.quit()
    
    def quit(self):
        """Quit the application"""
        self.running = False
        
        # Exit current screen
        if self.current_screen:
            self.screens[self.current_screen].exit()
        
        # Stop touch handler
        self.touch_handler.stop()
        
        # Cleanup console
        self.canvas.cleanup_console()
        print("Goodbye!")
        sys.exit(0)

if __name__ == "__main__":
    print("Starting ASCII Touch UI...")
    print("Initializing system...")
    print("-" * 40)
    
    # Check terminal size
    try:
        rows, cols = os.popen('stty size', 'r').read().split()
        print(f"Terminal size: {cols}x{rows}")
        if int(rows) < 24 or int(cols) < 80:
            print("Warning: Terminal size is small. Recommend at least 80x24.")
            print("You can resize with: stty cols 80 rows 24")
    except:
        print("Could not detect terminal size")
    
    time.sleep(1)
    
    try:
        app = Application()
        app.run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

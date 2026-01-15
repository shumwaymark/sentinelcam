"""Motion detector calibration tool for Sentinelcam watchtower

Provides real-time visual feedback for tuning motion detection parameters.
Self-contained calibration interface that operates independently of the Player subsystem.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import json
import logging
import cv2
import numpy as np
import tkinter as tk
import PIL.Image, PIL.ImageTk
import simplejpeg
from datetime import datetime
from sentinelcam.utils import ImageSubscriber

logger = logging.getLogger("watchtower.motion_calibration")


def blank_image(w, h) -> np.ndarray:
    """Create a blank black image"""
    return np.zeros((h, w, 3), dtype=np.uint8)


def convert_tkImage(cv2Image) -> PIL.ImageTk.PhotoImage:
    """Convert OpenCV BGR image to tkinter PhotoImage"""
    return PIL.ImageTk.PhotoImage(image=PIL.Image.fromarray(cv2.cvtColor(cv2Image, cv2.COLOR_BGR2RGB)))


class MotionCalibrator:
    """Motion detection parameter processor

    Applies MOG2 background subtraction with configurable parameters
    and annotates frames with motion detection results.
    """

    def __init__(self):
        self.params = {
            'varThreshold': 96,
            'detectShadows': False,
            'history': 500,
            'minContourW': 21,
            'minContourH': 21,
            'gaussianBlur': 5,
            'noMotionThreshold': 5
        }
        self.mog = self._build_mog()

    def _build_mog(self):
        """Build MOG2 background subtractor with current parameters"""
        return cv2.createBackgroundSubtractorMOG2(
            history=self.params['history'],
            varThreshold=self.params['varThreshold'],
            detectShadows=self.params['detectShadows']
        )

    def update_params(self, new_params):
        """Update parameters and rebuild MOG if necessary"""
        rebuild = any(k in new_params for k in ['varThreshold', 'history'])
        self.params.update(new_params)
        if rebuild:
            self.mog = self._build_mog()

    def process_frame(self, frame):
        """Apply motion detection and return annotated frame

        Args:
            frame: Input BGR image

        Returns:
            tuple: (annotated_frame, mask, has_motion)
                - annotated_frame: Frame with motion rectangles overlaid
                - mask: Binary motion mask
                - has_motion: Boolean indicating if valid motion was detected
        """
        # Convert and blur
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        ksize = self.params['gaussianBlur']
        gray = cv2.GaussianBlur(gray, (ksize, ksize), 0)

        # Apply MOG
        mask = self.mog.apply(gray)

        # Find contours
        cnts = cv2.findContours(mask.copy(), cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_SIMPLE)
        cnts = cnts[0] if len(cnts) == 2 else cnts[1]

        # Annotate
        result = frame.copy()
        valid_count = 0

        for c in cnts:
            (x, y, w, h) = cv2.boundingRect(c)

            if (w >= self.params['minContourW'] and
                h >= self.params['minContourH']):
                # Valid motion - green
                cv2.rectangle(result, (x, y), (x + w, y + h), (0, 255, 0), 2)
                valid_count += 1
            else:
                # Too small - red
                cv2.rectangle(result, (x, y), (x + w, y + h), (0, 0, 255), 1)

        # Status overlay
        status = f"MOTION: {valid_count} objects" if valid_count > 0 else "NO MOTION"
        color = (0, 255, 0) if valid_count > 0 else (0, 0, 255)
        cv2.putText(result, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                   0.7, color, 2)

        return result, mask, valid_count > 0


class MotionCalibrationPage(tk.Canvas):
    """Motion detector calibration interface

    Provides real-time visual feedback for tuning motion detection parameters.
    Displays live feed with motion rectangles overlaid and interactive sliders
    for parameter adjustment.
    """

    def __init__(self, parent, outpost_views):
        tk.Canvas.__init__(self, parent, width=800, height=480, borderwidth=0,
                          highlightthickness=0, background="black")

        self.parent = parent
        self.outpost_views = outpost_views
        self.current_view = None
        self.calibrator = None

        # Live image display area (reduced to make room for controls)
        self.current_image = convert_tkImage(blank_image(640, 360))
        self.image = self.create_image(0, 0, anchor="nw", image=self.current_image)

        # Parameter controls (bottom 120px)
        control_y = 370

        # varThreshold slider
        self.create_text(20, control_y, text="Sensitivity:", anchor="w",
                        fill="white", font=('TkDefaultFont', 10))
        self.var_threshold = tk.IntVar(value=96)
        self.var_slider = tk.Scale(self, from_=16, to=200, orient=tk.HORIZONTAL,
                                   variable=self.var_threshold, length=150,
                                   command=self.on_param_change)
        self.create_window(120, control_y, window=self.var_slider, anchor="w")

        # minContour slider
        self.create_text(300, control_y, text="Min Size:", anchor="w",
                        fill="white", font=('TkDefaultFont', 10))
        self.min_contour = tk.IntVar(value=21)
        self.min_slider = tk.Scale(self, from_=5, to=100, orient=tk.HORIZONTAL,
                                   variable=self.min_contour, length=150,
                                   command=self.on_param_change)
        self.create_window(380, control_y, window=self.min_slider, anchor="w")

        # noMotion threshold slider
        control_y += 40
        self.create_text(20, control_y, text="Quiet Frames:", anchor="w",
                        fill="white", font=('TkDefaultFont', 10))
        self.no_motion_thresh = tk.IntVar(value=5)
        self.no_motion_slider = tk.Scale(self, from_=1, to=20, orient=tk.HORIZONTAL,
                                        variable=self.no_motion_thresh, length=150,
                                        command=self.on_param_change)
        self.create_window(120, control_y, window=self.no_motion_slider, anchor="w")

        # Gaussian blur slider
        self.create_text(300, control_y, text="Blur:", anchor="w",
                        fill="white", font=('TkDefaultFont', 10))
        self.blur_size = tk.IntVar(value=5)
        self.blur_slider = tk.Scale(self, from_=3, to=15, resolution=2,
                                    orient=tk.HORIZONTAL, variable=self.blur_size,
                                    length=150, command=self.on_param_change)
        self.create_window(380, control_y, window=self.blur_slider, anchor="w")

        # Action buttons (right side)
        button_x = 680

        # Save button
        self.save_btn = tk.Button(self, text="SAVE", command=self.save_config,
                                 bg='green', fg='white', font=('TkDefaultFont', 10, 'bold'),
                                 width=8, height=2)
        self.create_window(button_x, 20, window=self.save_btn, anchor="nw")

        # Reset button
        self.reset_btn = tk.Button(self, text="RESET", command=self.reset_params,
                                   bg='orange', fg='white', font=('TkDefaultFont', 10, 'bold'),
                                   width=8, height=2)
        self.create_window(button_x, 90, window=self.reset_btn, anchor="nw")

        # Close button
        self.close_img = PIL.ImageTk.PhotoImage(file="images/close.png")
        id = self.create_image(730, 160, anchor="nw", image=self.close_img)
        self.tag_bind(id, "<Button-1>", lambda e: self.stop_calibration())

        # Status text
        self.status_text = self.create_text(400, 460, text="Select a view to calibrate",
                                           fill="chartreuse", font=('TkDefaultFont', 11))

        self.receiver = None
        self.update_running = False
        self.paused = False

    def start_calibration(self, viewname):
        """Start calibration for a specific view"""
        self.current_view = viewname
        view = self.outpost_views[viewname]

        # Store the view's native resolution for proper display
        self.view_size = view.imgsize

        # Initialize motion detector with current params
        self.calibrator = MotionCalibrator()

        # Start image subscription
        if self.receiver:
            self.receiver.stop()
        self.receiver = ImageSubscriber(view.publisher, view.view)
        self.receiver.start()

        # Start update loop
        self.update_running = True
        self.update_display()

        self.itemconfig(self.status_text,
                       text=f"Calibrating: {view.description} ({self.view_size[0]}x{self.view_size[1]})")

    def pause_calibration(self):
        """Pause calibration (called during inactivity timeout)"""
        if not self.paused:
            logger.info("Pausing motion calibration")
            self.paused = True
            self.update_running = False
            if self.receiver:
                self.receiver.stop()

    def resume_calibration(self):
        """Resume calibration after pause - maintains original view being calibrated"""
        if self.paused and self.current_view:
            # Resume with the ORIGINAL view that was being calibrated
            # User must explicitly close to calibrate a different view
            view = self.outpost_views[self.current_view]
            logger.info(f"Resuming motion calibration for {view.node}/{view.view}")

            self.paused = False

            # Restart the existing receiver (ImageSubscriber supports stop/start cycles)
            if self.receiver:
                self.receiver.start()
            else:
                logger.warning("No receiver to resume - calibration not properly initialized")
                return

            # Restore status display showing which view is being calibrated
            self.itemconfig(self.status_text,
                           text=f"Calibrating: {view.description} ({self.view_size[0]}x{self.view_size[1]})",
                           fill="chartreuse")

            # Restart update loop
            self.update_running = True
            self.update_display()

            logger.debug(f"Motion calibration resumed successfully")

    def stop_calibration(self):
        """Stop calibration completely and return to main page"""
        logger.info("Stopping motion calibration")
        self.update_running = False
        self.paused = False
        if self.receiver:
            self.receiver.stop()
            self.receiver = None

        # Reset calibrator
        self.calibrator = None
        self.current_view = None

        # Return to player page and ensure it's playing
        self.parent.show_page(0)  # UserPage.PLAYER = 0
        if self.parent.player_panel.paused:
            self.parent.player_panel.play()

    def on_param_change(self, value):
        """Handle slider changes"""
        if self.calibrator:
            # Ensure gaussian blur is always odd
            blur = self.blur_size.get()
            if blur % 2 == 0:
                blur = blur + 1
                self.blur_size.set(blur)

            self.calibrator.update_params({
                'varThreshold': self.var_threshold.get(),
                'minContourW': self.min_contour.get(),
                'minContourH': self.min_contour.get(),
                'gaussianBlur': blur,
                'noMotionThreshold': self.no_motion_thresh.get()
            })

    def update_display(self):
        """Update calibration display with annotated frame"""
        if not self.update_running:
            logger.debug("Update display stopped - update_running is False")
            return

        try:
            if self.receiver:
                msg, jpg = self.receiver.receive(timeout=1.0)
                frame = simplejpeg.decode_jpeg(jpg, colorspace='BGR')

                # Apply motion detection with current parameters
                annotated, mask, motion = self.calibrator.process_frame(frame)

                # Display at native view resolution - no resize needed
                # The view_size was set when calibration started
                self.current_image = convert_tkImage(annotated)
                self.itemconfig(self.image, image=self.current_image)
            else:
                logger.warning("Update display called but receiver is None")

        except TimeoutError:
            logger.debug("Timeout waiting for frame from receiver")
        except Exception as e:
            logger.exception(f"Calibration update error: {str(e)}")

        # Schedule next update
        if self.update_running:
            self.after(50, self.update_display)  # ~20fps

    def save_config(self):
        """Save calibrated parameters to outpost config"""
        if not self.current_view:
            return

        view = self.outpost_views[self.current_view]

        # Validate parameters before saving
        params = self.calibrator.params
        errors = []

        # varThreshold: 16-200 (sensitivity)
        if not (16 <= params['varThreshold'] <= 200):
            errors.append(f"varThreshold {params['varThreshold']} out of range 16-200")

        # minContour: 5-100 (minimum object size)
        if not (5 <= params['minContourW'] <= 100 and 5 <= params['minContourH'] <= 100):
            errors.append(f"minContour {params['minContourW']}/{params['minContourH']} out of range 5-100")

        # gaussianBlur: 3-15, must be odd
        blur = params['gaussianBlur']
        if not (3 <= blur <= 15 and blur % 2 == 1):
            errors.append(f"gaussianBlur {blur} must be odd number 3-15")

        # noMotionThreshold: 1-20 (frames before declaring no motion)
        if not (1 <= params['noMotionThreshold'] <= 20):
            errors.append(f"noMotionThreshold {params['noMotionThreshold']} out of range 1-20")

        # history: 1-1000 (MOG2 background model frames)
        if not (1 <= params['history'] <= 1000):
            errors.append(f"history {params['history']} out of range 1-1000")

        if errors:
            error_msg = "; ".join(errors)
            logger.error(f"Invalid motion parameters: {error_msg}")
            self.itemconfig(self.status_text,
                           text=f"Validation error - check parameters", fill="red")
            self.after(3000, lambda: self.itemconfig(self.status_text,
                       text=f"Calibrating: {view.description}", fill="chartreuse"))
            return

        config_path = os.path.join(
            os.path.expanduser("~"),
            "motion_configs",
            f"{view.node}_{view.view}.json"
        )

        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        config = {
            'viewname': view.view,
            'node': view.node,
            'size': view.imgsize,
            'motion_params': self.calibrator.params,
            'calibrated_at': datetime.now().isoformat()
        }

        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        # Visual feedback
        self.itemconfig(self.status_text,
                       text=f"Saved to {config_path}", fill="green")
        self.after(2000, lambda: self.itemconfig(self.status_text,
                   text=f"Calibrating: {view.description}", fill="chartreuse"))

        logger.info(f"Saved motion config to {config_path}")

    def reset_params(self):
        """Reset parameters to defaults"""
        self.var_threshold.set(96)
        self.min_contour.set(21)
        self.no_motion_thresh.set(5)
        self.blur_size.set(5)
        self.on_param_change(None)

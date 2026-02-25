"""Motion detector calibration tool for Sentinelcam watchtower

Provides real-time visual feedback for tuning motion detection parameters.
Calibration display mode layered on top of the existing Player subsystem.

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
from datetime import datetime

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
    and annotates frames with motion detection results. Supports ROI
    (Region of Interest) specified as percentage-based coordinates
    matching the outpost convention: (X1,Y1),(X2,Y2) in integer
    percent values from 0 to 100.
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
        # ROI as percentage-based coordinates: (x1,y1),(x2,y2)
        # Default (0,0),(100,100) means full frame
        self.roi_pct = (0, 0, 100, 100)
        self.mog = self._build_mog()

    def _build_mog(self):
        """Build MOG2 background subtractor with current parameters"""
        return cv2.createBackgroundSubtractorMOG2(
            history=self.params['history'],
            varThreshold=self.params['varThreshold'],
            detectShadows=self.params['detectShadows']
        )

    def update_roi(self, x1, y1, x2, y2):
        """Update ROI percentage coordinates

        Args:
            x1, y1: Top-left corner as integer percentages (0-100)
            x2, y2: Bottom-right corner as integer percentages (0-100)
        """
        self.roi_pct = (x1, y1, x2, y2)

    def _roi_is_default(self):
        """Check if ROI covers the full frame"""
        return self.roi_pct == (0, 0, 100, 100)

    def _roi_pixels(self, frame_h, frame_w):
        """Convert ROI percentages to pixel coordinates"""
        x1 = self.roi_pct[0] * frame_w // 100
        y1 = self.roi_pct[1] * frame_h // 100
        x2 = self.roi_pct[2] * frame_w // 100
        y2 = self.roi_pct[3] * frame_h // 100
        return x1, y1, x2, y2

    def update_params(self, new_params):
        """Update parameters and rebuild MOG if necessary"""
        rebuild = any(k in new_params for k in ['varThreshold', 'history'])
        self.params.update(new_params)
        if rebuild:
            self.mog = self._build_mog()

    def process_frame(self, frame):
        """Apply motion detection within the ROI and return annotated frame

        Motion detection is applied only within the configured ROI region.
        Contour coordinates are offset back to full-frame position for display.
        When the ROI is not the full frame, a translucent overlay is rendered
        over the excluded area.

        Args:
            frame: Input BGR image

        Returns:
            tuple: (annotated_frame, mask, has_motion)
                - annotated_frame: Frame with ROI overlay and motion rectangles
                - mask: Binary motion mask (ROI-sized)
                - has_motion: Boolean indicating if valid motion was detected
        """
        frame_h, frame_w = frame.shape[:2]
        rx1, ry1, rx2, ry2 = self._roi_pixels(frame_h, frame_w)

        # Extract ROI sub-region for motion detection
        roi = frame[ry1:ry2, rx1:rx2]

        # Convert and blur within ROI only
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        ksize = self.params['gaussianBlur']
        gray = cv2.GaussianBlur(gray, (ksize, ksize), 0)

        # Apply MOG to ROI
        mask = self.mog.apply(gray)

        # Find contours within ROI
        cnts = cv2.findContours(mask.copy(), cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_SIMPLE)
        cnts = cnts[0] if len(cnts) == 2 else cnts[1]

        # Build annotated result on full frame
        result = frame.copy()

        # Draw translucent ROI overlay when not full-frame
        if not self._roi_is_default():
            overlay = result.copy()
            # Fill excluded regions with a light bluish-gray tint
            # by drawing filled rectangles over the areas outside the ROI
            tint_color = (200, 190, 170)  # light bluish-gray in BGR
            # Top strip
            if ry1 > 0:
                cv2.rectangle(overlay, (0, 0), (frame_w, ry1), tint_color, -1)
            # Bottom strip
            if ry2 < frame_h:
                cv2.rectangle(overlay, (0, ry2), (frame_w, frame_h), tint_color, -1)
            # Left strip (between top and bottom)
            if rx1 > 0:
                cv2.rectangle(overlay, (0, ry1), (rx1, ry2), tint_color, -1)
            # Right strip (between top and bottom)
            if rx2 < frame_w:
                cv2.rectangle(overlay, (rx2, ry1), (frame_w, ry2), tint_color, -1)
            # Blend: 70% overlay, 30% original for translucent effect
            cv2.addWeighted(overlay, 0.35, result, 0.65, 0, result)

        # Annotate contours — offset coordinates from ROI back to full frame
        valid_count = 0

        for c in cnts:
            (cx, cy, cw, ch) = cv2.boundingRect(c)
            # Offset to full-frame coordinates
            x = cx + rx1
            y = cy + ry1

            if (cw >= self.params['minContourW'] and
                ch >= self.params['minContourH']):
                # Valid motion - green
                cv2.rectangle(result, (x, y), (x + cw, y + ch), (0, 255, 0), 2)
                valid_count += 1
            else:
                # Too small - red
                cv2.rectangle(result, (x, y), (x + cw, y + ch), (0, 0, 255), 1)

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

    def __init__(self, app, outpost_views):
        tk.Canvas.__init__(self, app.master, width=800, height=480, borderwidth=0,
                          highlightthickness=0, background="black")

        self.app = app
        self.outpost_views = outpost_views
        self.current_view = None
        self.calibrator = None
        self.active = False

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

        # ROI controls (next row)
        control_y += 40
        self.create_text(20, control_y, text="ROI:", anchor="w",
                        fill="white", font=('TkDefaultFont', 10))

        # ROI corner sliders — percentage-based (0-100)
        self.roi_x1 = tk.IntVar(value=0)
        self.roi_y1 = tk.IntVar(value=0)
        self.roi_x2 = tk.IntVar(value=100)
        self.roi_y2 = tk.IntVar(value=100)

        lbl_font = ('TkDefaultFont', 8)
        slider_len = 80

        self.create_text(60, control_y, text="X1:", anchor="w",
                        fill="#AAAAAA", font=lbl_font)
        roi_x1_slider = tk.Scale(self, from_=0, to=95, orient=tk.HORIZONTAL,
                                  variable=self.roi_x1, length=slider_len,
                                  command=self.on_roi_change)
        self.create_window(85, control_y, window=roi_x1_slider, anchor="w")

        self.create_text(190, control_y, text="Y1:", anchor="w",
                        fill="#AAAAAA", font=lbl_font)
        roi_y1_slider = tk.Scale(self, from_=0, to=95, orient=tk.HORIZONTAL,
                                  variable=self.roi_y1, length=slider_len,
                                  command=self.on_roi_change)
        self.create_window(215, control_y, window=roi_y1_slider, anchor="w")

        self.create_text(320, control_y, text="X2:", anchor="w",
                        fill="#AAAAAA", font=lbl_font)
        roi_x2_slider = tk.Scale(self, from_=5, to=100, orient=tk.HORIZONTAL,
                                  variable=self.roi_x2, length=slider_len,
                                  command=self.on_roi_change)
        self.create_window(345, control_y, window=roi_x2_slider, anchor="w")

        self.create_text(450, control_y, text="Y2:", anchor="w",
                        fill="#AAAAAA", font=lbl_font)
        roi_y2_slider = tk.Scale(self, from_=5, to=100, orient=tk.HORIZONTAL,
                                  variable=self.roi_y2, length=slider_len,
                                  command=self.on_roi_change)
        self.create_window(475, control_y, window=roi_y2_slider, anchor="w")

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

    def start_calibration(self, viewname):
        """Start calibration for a specific view"""
        self.current_view = viewname
        view = self.outpost_views[viewname]
        self.view_size = view.imgsize
        self.calibrator = MotionCalibrator()
        self.active = True
        # Load this view into the PlayerDaemon without switching page
        self.app._load_viewer_source(viewname)
        self.itemconfig(self.status_text,
                       text=f"Calibrating: {view.description} ({self.view_size[0]}x{self.view_size[1]})")

    def pause_calibration(self):
        """Pause calibration when navigating away or on inactivity timeout"""
        if self.active:
            logger.info("Pausing motion calibration")
            self.active = False

    def resume_calibration(self):
        """Resume calibration after pause - maintains original view being calibrated"""
        if not self.active and self.current_view and self.calibrator:
            view = self.outpost_views[self.current_view]
            logger.info(f"Resuming motion calibration for {view.node}/{view.view}")
            self.active = True
            self.itemconfig(self.status_text,
                           text=f"Calibrating: {view.description} ({self.view_size[0]}x{self.view_size[1]})",
                           fill="chartreuse")
            logger.debug(f"Motion calibration resumed successfully")

    def stop_calibration(self):
        """Stop calibration completely and return to player page"""
        logger.info("Stopping motion calibration")
        self.active = False
        self.calibrator = None
        self.current_view = None
        self.app.show_page(0)  # UserPage.PLAYER = 0

    def on_frame(self, image):
        """Receive a frame from Application.update() and display with motion overlay"""
        if not self.active or self.calibrator is None:
            return
        annotated, mask, motion = self.calibrator.process_frame(image)
        self.current_image = convert_tkImage(annotated)
        self.itemconfig(self.image, image=self.current_image)

    def on_roi_change(self, value):
        """Handle ROI slider changes, enforcing x1<x2 and y1<y2"""
        if self.calibrator:
            x1 = self.roi_x1.get()
            y1 = self.roi_y1.get()
            x2 = self.roi_x2.get()
            y2 = self.roi_y2.get()
            # Enforce minimum separation
            if x2 <= x1:
                x2 = min(x1 + 5, 100)
                self.roi_x2.set(x2)
            if y2 <= y1:
                y2 = min(y1 + 5, 100)
                self.roi_y2.set(y2)
            self.calibrator.update_roi(x1, y1, x2, y2)

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

        roi = self.calibrator.roi_pct
        config = {
            'viewname': view.view,
            'node': view.node,
            'size': view.imgsize,
            'motion_params': self.calibrator.params,
            'ROI': f"({roi[0]},{roi[1]}),({roi[2]},{roi[3]})",
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
        self.roi_x1.set(0)
        self.roi_y1.set(0)
        self.roi_x2.set(100)
        self.roi_y2.set(100)
        self.on_param_change(None)
        self.on_roi_change(None)

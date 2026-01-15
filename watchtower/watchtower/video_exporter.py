"""video_exporter: Export watchtower events as MP4 video with tracking overlays

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import cv2
import hashlib
import json
import numpy as np
import pandas as pd
import logging
import multiprocessing
import requests
import subprocess
import simplejpeg
import time
from base64 import b64encode
from datetime import datetime, timedelta
#from urllib import request, parse, error
from sentinelcam.datafeed import DataFeed

logger = logging.getLogger("watchtower.video_exporter")

class TextHelper:
    """Helper for drawing tracking overlays on video frames

    Isolated from main watchtower module to avoid Tkinter/X11 threading issues
    in multiprocessing subprocess.
    """
    def __init__(self) -> None:
        self._lineType = cv2.LINE_AA
        self._textType = cv2.FONT_HERSHEY_SIMPLEX
        self._textSize = 0.5
        self._thickness = 1
        self._textColors = {}
        self._bboxColors = {}
        self.setColors(['Unknown'])

    def setTextColor(self, bgr) -> tuple:
        luminance = ((bgr[0]*.114)+(bgr[1]*.587)+(bgr[2]*.299))/255
        return (0,0,0) if luminance > 0.5 else (255,255,255)

    def setColors(self, names) -> None:
        for name in names:
            if name not in self._bboxColors:
                self._bboxColors[name] = tuple(int(x) for x in np.random.randint(256, size=3))
                self._textColors[name] = self.setTextColor(self._bboxColors[name])

    def putText(self, frame, objid, text, x1, y1, x2, y2) -> None:
        (tw, th) = cv2.getTextSize(text, self._textType, self._textSize, self._thickness)[0]
        cv2.rectangle(frame, (x1, y1), (x2, y2), self._bboxColors[objid], 2)
        cv2.rectangle(frame, (x1, (y1 - 28)), ((x1 + tw + 10), y1), self._bboxColors[objid], cv2.FILLED)
        cv2.putText(frame, text, (x1 + 5, y1 - 10), self._textType, self._textSize, self._textColors[objid], self._thickness, self._lineType)

class VideoExporter:
    """Exports watchtower events as MP4 video files with automatic event merging"""

    def __init__(self, config, outpost_views):
        """Initialize video exporter subprocess

        Args:
            config: video_export configuration dictionary
            outpost_views: Dictionary of OutpostView objects for event context
        """
        self.config = config
        self.outpost_views = outpost_views
        self.export_queue = multiprocessing.Queue()
        self.progress_queue = multiprocessing.Queue()
        self.process = multiprocessing.Process(
            target=self._export_worker,
            args=(self.export_queue, self.progress_queue, config)
        )
        self.process.daemon = True
        self.process.start()
        logger.info("VideoExporter subprocess started")

    def export_event(self, viewname, date, event, datapump):
        """Queue an event for export

        Args:
            viewname: Camera view name
            date: Event date string
            event: Event ID
            datapump: DataFeed connection string
        """
        self.export_queue.put(('EXPORT', viewname, date, event, datapump))
        logger.debug(f"Queued export for {viewname} {date}/{event}")

    @staticmethod
    def _export_worker(export_q, progress_q, config):
        """Subprocess worker that handles video export

        Args:
            export_q: Queue receiving export requests
            progress_q: Queue for sending progress updates
            config: Export configuration
        """
        worker_logger = logging.getLogger("watchtower.video_exporter.worker")
        worker_logger.info("Export worker started")

        # Extract configuration
        output_dir = config.get('output_dir', 'datasink:/tmp/videos')
        max_gap_seconds = config.get('max_event_gap_seconds', 30)
        max_merged_events = config.get('max_merged_events', 10)
        include_overlays = config.get('include_overlays', True)
        header_frames = config.get('header_duration_frames', 60)
        adaptive_text = config.get('adaptive_text_color', True)
        temp_dir = config.get('local_temp_dir', '/tmp/watchtower_exports')

        # Site description for labeling (optional)
        site_description = config.get('site_description', 'SentinelCam')

        # VPS upload configuration (optional)
        vps_config = config.get('vps_upload', {})
        vps_enabled = vps_config.get('enabled', False)

        # Notification configuration (optional)
        notif_config = config.get('notifications', {})

        # Create temp directory
        os.makedirs(temp_dir, exist_ok=True)

        # Connection cache
        datafeeds = {}

        while True:
            try:
                task = export_q.get()
                if task[0] != 'EXPORT':
                    continue

                _, viewname, start_date, start_event, datapump = task

                # Phase 1: Detecting sequential events
                progress_q.put(('PHASE', 'Detecting', 0, 1))
                worker_logger.info(f"Starting export for {viewname} {start_date}/{start_event}")

                # Get DataFeed connection
                if datapump not in datafeeds:
                    datafeeds[datapump] = DataFeed(datapump, timeout=7.0)
                feed = datafeeds[datapump]

                # Detect sequential events
                events_to_merge = VideoExporter._detect_sequential_events(
                    feed, start_date, start_event, viewname,
                    max_gap_seconds, max_merged_events, worker_logger
                )

                worker_logger.info(f"Found {len(events_to_merge)} sequential events to merge")

                # Phase 2: Rendering video
                progress_q.put(('PHASE', 'Rendering', 0, 1))

                # Generate output filename, use first 8 chars of event ID (short hash style)
                start_timestamp = events_to_merge[0][2]
                start_time = start_timestamp.strftime('%Y%m%d_%H%M%S')
                event_short = start_event[:8] if len(start_event) >= 8 else start_event
                filename = f"{viewname}_{start_time}_{event_short}.mp4"
                local_path = os.path.join(temp_dir, filename)

                # Render video
                total_frames = VideoExporter._render_video(
                    viewname, feed, events_to_merge, local_path, include_overlays,
                    header_frames, adaptive_text, progress_q, worker_logger
                )

                if total_frames == 0:
                    progress_q.put(('ERROR', 'No frames rendered'))
                    continue

                # Phase 3: Optimize for web streaming (before transfer)
                progress_q.put(('PHASE', 'Optimizing', 0, 1))
                worker_logger.info("Optimizing video for web streaming...")
                temp_path = local_path[:-4] + "_ffmpeg.mp4"
                try:
                    #   'ffmpeg', '-i', local_path, '-c', 'copy', '-movflags', '+faststart',
                    #   '-y', temp_path

                    #   'ffmpeg', '-i', local_path, '-c:v', 'libx264', '-profile:v', 'high', '-level', '4.0',
                    #   '-pix_fmt', 'yuv420p', '-crf', '23', '-c:a', 'aac', '-ar', '48000', '-movflags', '+faststart',
                    #   '-y', temp_path

                    subprocess.run([
                        'ffmpeg', '-i', local_path, '-c:v', 'libx264', '-profile:v', 'baseline', '-level', '3.0',
                        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                        '-y', temp_path
                    ], check=True, capture_output=True)
                    os.replace(temp_path, local_path)
                    worker_logger.info("Video optimized for streaming")
                except subprocess.CalledProcessError as e:
                    worker_logger.warning(f"Could not optimize video for streaming: {e.stderr.decode()}")
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception as e:
                    worker_logger.warning(f"Could not optimize video for streaming: {str(e)}")
                    if os.path.exists(temp_path):
                        os.remove(temp_path)

                # Phase 4: Transferring to datasink
                progress_q.put(('PHASE', 'Transferring', 0, 1))
                worker_logger.info(f"Transferring {filename} to {output_dir}")

                try:
                    # Transfer via scp
                    remote_path = f"{output_dir}/{filename}"
                    result = subprocess.run(
                        ['scp', local_path, remote_path],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )

                    if result.returncode == 0:
                        worker_logger.info(f"Successfully transferred {filename}")

                        # Phase 4: Upload to VPS (optional)
                        if vps_enabled:
                            vps_success = VideoExporter._upload_to_vps(
                                local_path, filename, vps_config, progress_q, worker_logger
                            )

                            # Phase 5: Send notification (optional)
                            if vps_success and notif_config:
                                VideoExporter._send_notification(
                                    filename, viewname, start_timestamp, len(events_to_merge), total_frames,
                                    vps_config, notif_config, site_description, worker_logger
                                )

                        # Clean up local file
                        os.remove(local_path)
                        progress_q.put(('COMPLETE', filename, len(events_to_merge), total_frames))
                    else:
                        worker_logger.error(f"SCP transfer failed: {result.stderr}")
                        progress_q.put(('ERROR', f"Transfer failed: {result.stderr}"))

                except subprocess.TimeoutExpired:
                    worker_logger.error("SCP transfer timed out")
                    progress_q.put(('ERROR', 'Transfer timed out'))
                except Exception as e:
                    worker_logger.exception(f"Transfer error: {str(e)}")
                    progress_q.put(('ERROR', f'Transfer error: {str(e)}'))

            except Exception as e:
                worker_logger.exception(f"Export worker error: {str(e)}")
                progress_q.put(('ERROR', str(e)))

    @staticmethod
    def _detect_sequential_events(feed, start_date, start_event, viewname,
                                   max_gap_seconds, max_merged_events, logger):
        """Detect sequential events to merge starting from selected event

        Scans backward to find earliest event, then forward until gap threshold exceeded.

        Returns:
            List of (date, event, imgsize) tuples in chronological order
        """
        try:
            cwIndx = feed.get_date_index(start_date).sort_values('timestamp')
            trk_events = cwIndx.loc[
                (cwIndx['type'] == 'trk') &
                (cwIndx['viewname'] == viewname)
            ]

            if len(trk_events.index) == 0:
                return [(start_date, start_event, (640, 360))]

            # Find starting event index
            start_idx = trk_events.loc[trk_events['event'] == start_event].index
            if len(start_idx) == 0:
                return [(start_date, start_event, (640, 360))]
            start_idx = trk_events.index.get_loc(start_idx[0])

            # Scan backward to find first event in sequence
            # Gap is measured between consecutive event start times
            first_idx = start_idx
            for i in range(start_idx - 1, -1, -1):
                prev_time = trk_events.iloc[i]['timestamp']
                curr_time = trk_events.iloc[i + 1]['timestamp']
                gap = (curr_time - prev_time).total_seconds()

                if gap > max_gap_seconds:
                    break
                first_idx = i

                if start_idx - first_idx >= max_merged_events - 1:
                    break

            # Scan forward from first event
            # Gap is measured between consecutive event start times
            events = []
            prev_time = None

            for i in range(first_idx, len(trk_events.index)):
                evt = trk_events.iloc[i]

                if prev_time is not None:
                    gap = (evt['timestamp'] - prev_time).total_seconds()
                    if gap > max_gap_seconds:
                        break

                events.append((
                    start_date,
                    evt['event'],
                    evt['timestamp'],
                    (int(evt['width']), int(evt['height']))
                ))

                prev_time = evt['timestamp']

                if len(events) >= max_merged_events:
                    break

            logger.info(f"Sequential events: {len(events)} events spanning indices {first_idx} to {first_idx + len(events) - 1}")
            return events

        except Exception as e:
            logger.exception(f"Error detecting sequential events: {str(e)}")
            return [(start_date, start_event, (640, 360))]

    @staticmethod
    def _render_video(viewname, feed, events_to_merge, output_path, include_overlays,
                     header_frames, adaptive_text, progress_q, logger):
        """Render video from merged events

        Returns:
            Total number of frames rendered
        """
        try:
            # Phase: Loading frames and tracking data
            progress_q.put(('PHASE', 'Loading', 0, 1))

            # Gather all frames and tracking data across events
            all_frames_data = []
            sub_events = []  # Track sub-event boundaries for headers

            for event_idx, (date, event, starttime, imgsize) in enumerate(events_to_merge):
                # Update progress during loading
                progress_q.put(('PROGRESS', 'Loading', event_idx + 1, len(events_to_merge)))
                try:
                    frametimes = feed.get_image_list(date, event)

                    # Get tracking data if overlays enabled
                    tracking_data = []
                    if include_overlays:
                        try:
                            refsort = {'trk': 0, 'obj': 1, 'vsp': 2, 'fd1': 3, 'fr1': 4}
                            cwIndx = feed.get_date_index(date)
                            evtSets = cwIndx.loc[cwIndx['event'] == event]

                            if len(evtSets.index) > 0:
                                trkTypes = [t for t in evtSets['type']]
                                all_tracking_data = []

                                for t in trkTypes:
                                    try:
                                        data = feed.get_tracking_data(date, event, t)
                                        all_tracking_data.append((t, data))
                                    except DataFeed.TrackingSetEmpty:
                                        pass

                                if all_tracking_data:
                                    evtData = pd.concat(
                                        [data for _, data in all_tracking_data],
                                        keys=[t for t, _ in all_tracking_data],
                                        names=['ref']
                                    )
                                    evtData['name'] = evtData.apply(lambda x: str(x['classname']).split(':')[0], axis=1)

                                    # Build tracking data per frame
                                    for frametime in frametimes:
                                        frame_data = tuple(
                                            (rec.name, rec.classname, rec.rect_x1, rec.rect_y1, rec.rect_x2, rec.rect_y2)
                                            for rec in evtData.loc[evtData['timestamp'] == frametime].sort_values(
                                                by=['ref'], key=lambda x: x.map(refsort)
                                            ).itertuples()
                                        )
                                        tracking_data.append(frame_data)
                        except Exception as e:
                            logger.warning(f"Could not load tracking data for {event}: {str(e)}")
                            tracking_data = [() for _ in frametimes]
                    else:
                        tracking_data = [() for _ in frametimes]

                    # Record sub-event boundary
                    sub_events.append((len(all_frames_data), frametimes[0], event))

                    # Add frames to master list
                    for i, frametime in enumerate(frametimes):
                        all_frames_data.append((date, event, frametime, tracking_data[i], imgsize))

                except Exception as e:
                    logger.warning(f"Could not process event {event}: {str(e)}")

            if len(all_frames_data) == 0:
                logger.error("No frames to render")
                return 0

            # Calculate FPS from consecutive frame intervals (excluding gaps between events)
            # This gives true capture rate regardless of event merging
            if len(all_frames_data) > 1:
                intervals = []
                for i in range(1, len(all_frames_data)):
                    prev_time = all_frames_data[i-1][2]
                    curr_time = all_frames_data[i][2]
                    interval = (curr_time - prev_time).total_seconds()
                    # Only include reasonable intervals (< 2 seconds) to skip event gaps
                    if 0 < interval < 2.0:
                        intervals.append(interval)

                if intervals:
                    avg_interval = sum(intervals) / len(intervals)
                    fps = 1.0 / avg_interval if avg_interval > 0 else 10.0
                    fps = max(5.0, min(fps, 30.0))  # Clamp to reasonable range
                else:
                    fps = 10.0  # Default if no valid intervals
            else:
                fps = 10.0

            logger.info(f"Rendering {len(all_frames_data)} frames at {fps:.2f} fps")

            # Phase: Now actually rendering frames
            progress_q.put(('PHASE', 'Rendering', 0, 1))

            # Get frame size from first event
            frame_width, frame_height = all_frames_data[0][4]

            # Initialize video writer
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(output_path, fourcc, fps, (frame_width, frame_height))

            # Initialize text helper for overlays (local class to avoid X11 issues)
            text_helper = TextHelper()

            # Pre-calculate text colors for sub-event headers if adaptive
            header_colors = {}
            if adaptive_text:
                for sub_idx, _, _ in sub_events:
                    if sub_idx < len(all_frames_data):
                        # Sample first frame of sub-event
                        date, event, frametime, _, imgsize = all_frames_data[sub_idx]
                        try:
                            jpeg = feed.get_image_jpg(date, event, frametime)
                            frame = simplejpeg.decode_jpeg(jpeg, colorspace='BGR')
                            header_colors[sub_idx] = VideoExporter._calculate_header_text_color(frame)
                        except:
                            header_colors[sub_idx] = (255, 255, 255)

            # Render frames
            frames_in_subevent = 0
            current_subevent_idx = 0
            subevent_start_idx, subevent_start_time, subevent_id = sub_events[0]

            for frame_idx, (date, event, frametime, tracking, imgsize) in enumerate(all_frames_data):
                # Check if we've moved to next sub-event
                if current_subevent_idx + 1 < len(sub_events):
                    next_start, _, _ = sub_events[current_subevent_idx + 1]
                    if frame_idx >= next_start:
                        current_subevent_idx += 1
                        subevent_start_idx, subevent_start_time, subevent_id = sub_events[current_subevent_idx]
                        frames_in_subevent = 0

                try:
                    # Fetch and decode frame
                    jpeg = feed.get_image_jpg(date, event, frametime)
                    frame = simplejpeg.decode_jpeg(jpeg, colorspace='BGR')

                    # Draw tracking overlays
                    if include_overlays and len(tracking) > 0:
                        # Get unique object names for color assignment
                        names = list(set(name for name, _, _, _, _, _ in tracking))
                        text_helper.setColors(names)

                        for name, classname, x1, y1, x2, y2 in tracking:
                            text_helper.putText(frame, name, classname, x1, y1, x2, y2)

                    # Draw sub-event header for first N frames
                    if frames_in_subevent < header_frames:
                        header_text = f"{viewname} {subevent_start_time.strftime('%I:%M %p - %A %B %d, %Y')}"
                        text_color = header_colors.get(subevent_start_idx, (255, 255, 255)) if adaptive_text else (255, 255, 255)

                        # Get text size for background rectangle
                        (text_width, text_height), baseline = cv2.getTextSize(
                            header_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1
                        )

                        # Draw semi-transparent background
                        overlay = frame.copy()
                        cv2.rectangle(overlay, (15, 20), (30 + text_width, 50 + baseline), (0, 0, 0), -1)
                        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

                        # Draw text on top
                        cv2.putText(frame, header_text, (20, 40),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color, 1)

                    writer.write(frame)
                    frames_in_subevent += 1

                    # Update progress every 10 frames
                    if frame_idx % 10 == 0:
                        progress_q.put(('PROGRESS', 'Rendering', frame_idx + 1, len(all_frames_data)))

                except Exception as e:
                    logger.warning(f"Could not render frame {frame_idx}: {str(e)}")

            writer.release()
            logger.info(f"Video rendered successfully: {len(all_frames_data)} frames")
            return len(all_frames_data)

        except Exception as e:
            logger.exception(f"Error rendering video: {str(e)}")
            return 0

    @staticmethod
    def _calculate_header_text_color(frame):
        """Calculate optimal text color based on frame brightness

        Samples top region where header text will be drawn.

        Returns:
            BGR tuple for text color (black or white)
        """
        # Sample the region where text will appear (top 50 pixels)
        region = frame[0:min(50, frame.shape[0]), :]
        # Calculate average BGR
        avg_bgr = np.mean(region, axis=(0, 1))
        # Calculate luminance
        luminance = ((avg_bgr[0] * 0.114) + (avg_bgr[1] * 0.587) + (avg_bgr[2] * 0.299)) / 255
        # Return black for bright backgrounds, white for dark
        return (0, 0, 0) if luminance > 0.5 else (255, 255, 255)

    @staticmethod
    def _upload_to_vps(local_path, filename, vps_config, progress_q, logger):
        """Upload video file to VPS for external sharing

        Args:
            local_path: Local file path
            filename: Video filename
            vps_config: VPS configuration dict
            progress_q: Queue for progress updates
            logger: Logger instance

        Returns:
            bool: True if upload successful, False otherwise
        """
        try:
            progress_q.put(('PHASE', 'Uploading to VPS', 0, 1))
            logger.info(f"Uploading {filename} to VPS")

            # Build SCP command
            vps_host = vps_config['host']
            vps_user = vps_config.get('user', 'rocky')
            vps_port = vps_config.get('port', 22)
            vps_path = vps_config.get('path', '/var/www/sentinelcam_exports')

            remote_dest = f"{vps_user}@{vps_host}:{vps_path}/{filename}"

            result = subprocess.run(
                ['scp', '-P', str(vps_port), local_path, remote_dest],
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                logger.info(f"Successfully uploaded {filename} to VPS")
                return True
            else:
                logger.error(f"VPS upload failed: {result.stderr}")
                progress_q.put(('ERROR', f"VPS upload failed: {result.stderr}"))
                return False

        except subprocess.TimeoutExpired:
            logger.error("VPS upload timed out")
            progress_q.put(('ERROR', 'VPS upload timed out'))
            return False
        except Exception as e:
            logger.exception(f"VPS upload error: {str(e)}")
            progress_q.put(('ERROR', f'VPS upload error: {str(e)}'))
            return False

    @staticmethod
    def _send_notification(filename, viewname, start_timestamp, event_count, frame_count, vps_config, notif_config, site_description, logger):
        """Send Telegram notification with secure link to video

        Args:
            filename: Video filename
            viewname: Camera view name
            start_timestamp: Start timestamp of the video
            event_count: Number of events merged
            frame_count: Total frame count
            vps_config: VPS configuration dict
            notif_config: Notification configuration dict
            site_description: Site description string
            logger: Logger instance
        """
        try:
            logger.info("Generating secure link and sending notification")

            # Generate secure_link URL
            base_url = vps_config.get('base_url', 'https://yourvps.com/exports')
            secret = vps_config.get('secure_link_secret')
            expiry_hours = vps_config.get('link_expiry_hours', 24)

            expires = int(time.time()) + (expiry_hours * 3600)
            uri_path = f"/sentinelcam_exports/{filename}"

            # Calculate MD5: expires + path + secret (base64-encoded for nginx secure_link)
            md5_input = f"{expires}{uri_path}{secret}"
            md5_binary = hashlib.md5(md5_input.encode()).digest()
            md5_base64 = b64encode(md5_binary).decode().rstrip('=')  # nginx uses unpadded base64

            # Build secure URL
            secure_url = f"{base_url}/{filename}?md5={md5_base64}&expires={expires}"

            # Build Telegram message with Markdown formatting
            duration_sec = frame_count / 30  # Assuming 30fps
            message = (
                f"🎥 *{site_description} Video Export*\n\n"
                f"📹 View: `{viewname}`\n"
                f"🗓 Start Time: {start_timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"📊 Events: {event_count}\n"
                f"⏱ Duration: {duration_sec:.0f}s\n"
                f"⏰ Link expires: {datetime.fromtimestamp(expires).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"[Download Video]({secure_url})"
            )

            # Send via Telegram
            telegram_config = notif_config.get('telegram', {})
            bot_token = telegram_config.get('bot_token')
            chat_id = telegram_config.get('chat_id')

            if not bot_token or not chat_id:
                logger.warning("Incomplete Telegram configuration, skipping notification")
                return

            logger.debug(f"Telegram request ready, message length {len(message)} chat_id {chat_id}")
            VideoExporter._send_telegram_message(bot_token, chat_id, message, logger)

            logger.info(f"Sent notification to Telegram")

        except Exception as e:
            logger.exception(f"Notification error: {str(e)}")
            # Don't propagate - notification failure shouldn't block export

    @staticmethod
    def _send_telegram_message(bot_token, chat_id, message, logger):
        """Send message via Telegram Bot API

        Args:
            bot_token: Telegram bot token
            chat_id: Telegram chat ID (user or group)
            message: Message body (supports Markdown)
            logger: Logger instance
        """
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

            # Build POST data with Markdown support
            json = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'Markdown',
                'disable_web_page_preview': False
            }

            # Send request
            response = requests.post(url, json=json)
            if response.status_code == 200:
                logger.info(f"Telegram message sent to chat {chat_id}")
            else:
                logger.warning(f"Unexpected Telegram response: {response.status_code}")

        except Exception as e:
            logger.exception(f"Telegram send error: {str(e)}")

    def close(self):
        """Shutdown export subprocess"""
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=5)
            logger.info("VideoExporter subprocess terminated")

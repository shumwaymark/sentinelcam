"""speed_montage: Daily speed event montage generator.

Scans a single day's event data for an outpost view, identifies vehicle speed
(vsp) events exceeding a configured MPH cutoff, and renders a montage video.
Each qualifying speed event is bracketed by the immediately preceding and
following trk events from the same view to provide visual contrast.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import os
import re
import sys
import cv2
import hashlib
import logging
import argparse
import numpy as np
import pandas as pd
import requests
import subprocess
import simplejpeg
import time
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta
from sentinelcam.datafeed import DataFeed
from sentinelcam.utils import readConfig

logger = logging.getLogger("speed_montage")


class TextHelper:
    """Helper for drawing tracking overlays on video frames"""

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
        cv2.putText(frame, text, (x1 + 5, y1 - 10), self._textType, self._textSize,
                    self._textColors[objid], self._thickness, self._lineType)


class SpeedMontage:
    """Daily speed event montage generator"""

    def __init__(self, config):
        self.config = config

    def run(self, target_date, cutoff_override=None):
        """Main entry point. Returns True if video produced, False if no qualifying events."""
        montage_cfg = self.config.get('montage', {})
        cutoff = cutoff_override if cutoff_override is not None else montage_cfg.get('speed_cutoff_mph', 45.0)
        viewname = montage_cfg['viewname']

        datapump = self.config['datapump']
        feed = DataFeed(datapump, timeout=15.0)

        # Scan for qualifying speed events
        speed_events = self._scan_speed_events(feed, target_date, viewname, cutoff)

        if not speed_events:
            logger.info(f"No speed events above {cutoff} mph for {viewname} on {target_date}")
            return False

        speed_events.sort(key=lambda x: x[2])  # sort by timestamp
        peak_speed = max(s for _, s, _ in speed_events)
        logger.info(f"Found {len(speed_events)} qualifying speed events, peak: {peak_speed:.1f} mph")

        # Build event clusters with predecessor/successor context
        events = self._build_clusters(feed, target_date, viewname, speed_events)
        logger.info(f"Built cluster of {len(events)} events")

        # Prepare output path
        include_overlays = montage_cfg.get('include_overlays', True)
        header_frames = montage_cfg.get('header_duration_frames', 60)
        adaptive_text = montage_cfg.get('adaptive_text_color', True)

        output_cfg = self.config.get('output', {})
        temp_dir = output_cfg.get('local_temp_dir', '/tmp/speed_montage')
        os.makedirs(temp_dir, exist_ok=True)

        filename_pattern = output_cfg.get('filename_pattern', 'speed_{viewname}_{date}_{cutoff}mph')
        filename = filename_pattern.format(
            viewname=viewname,
            date=target_date,
            cutoff=int(cutoff)
        ) + '.mp4'
        local_path = os.path.join(temp_dir, filename)

        # Render montage
        frame_count, fps = self._render_montage(
            viewname, feed, events, local_path,
            include_overlays, header_frames, adaptive_text, logger
        )

        if frame_count == 0:
            logger.error("No frames rendered")
            return False

        # Optimize for web streaming
        self._optimize_video(local_path, logger)

        # Deliver: datasink transfer, optional VPS upload + notification
        self._deliver(
            local_path, filename,
            viewname, target_date, cutoff,
            len(speed_events), peak_speed, frame_count, fps,
            self.config, logger
        )

        return True

    def _scan_speed_events(self, feed, target_date, viewname, cutoff):
        """Scan vsp tracking data, return list of (event_id, max_speed, timestamp)
        for events exceeding cutoff."""
        try:
            cwIndx = feed.get_date_index(target_date)
            vsp_rows = cwIndx.loc[
                (cwIndx['type'] == 'vsp') &
                (cwIndx['viewname'] == viewname)
            ]
            qualifying = []
            for _, row in vsp_rows.iterrows():
                event_id = row['event']
                timestamp = row['timestamp']
                try:
                    tracking_df = feed.get_tracking_data(target_date, event_id, 'vsp')
                    max_speed = self._parse_max_speed(tracking_df)
                    if max_speed >= cutoff:
                        qualifying.append((event_id, max_speed, timestamp))
                except DataFeed.TrackingSetEmpty:
                    pass
            return qualifying
        except Exception as e:
            logger.exception(f"Error scanning speed events: {str(e)}")
            return []

    def _build_clusters(self, feed, target_date, viewname, speed_events):
        """Build event clusters: each speed event preceded by one normal trk event for contrast.
        Returns deduplicated ordered list of (date, event_id, timestamp, imgsize) tuples."""
        try:
            cwIndx = feed.get_date_index(target_date)
            trk_events = cwIndx.loc[
                (cwIndx['type'] == 'trk') &
                (cwIndx['viewname'] == viewname)
            ].sort_values('timestamp').reset_index(drop=True)

            clusters = []
            for event_id, max_speed, vsp_timestamp in speed_events:
                evt_cluster = []

                # Predecessor: closest trk event before this speed event (excluding same UUID)
                pred_candidates = trk_events.loc[
                    (trk_events['timestamp'] < vsp_timestamp) &
                    (trk_events['event'] != event_id)
                ]
                if len(pred_candidates) > 0:
                    pred_row = pred_candidates.iloc[-1]
                    evt_cluster.append((
                        target_date,
                        pred_row['event'],
                        pred_row['timestamp'],
                        (int(pred_row['width']), int(pred_row['height']))
                    ))

                # Speed event itself
                vsp_row = cwIndx.loc[cwIndx['event'] == event_id].iloc[0]
                evt_cluster.append((
                    target_date,
                    event_id,
                    vsp_timestamp,
                    (int(vsp_row['width']), int(vsp_row['height']))
                ))

                clusters.append(evt_cluster)

            # Deduplicate: collect all events across clusters in order, drop duplicates
            seen = set()
            merged = []
            for cluster in clusters:
                for item in cluster:
                    evt_id = item[1]
                    if evt_id not in seen:
                        seen.add(evt_id)
                        merged.append(item)

            # Sort chronologically
            merged.sort(key=lambda x: x[2])
            return merged

        except Exception as e:
            logger.exception(f"Error building clusters: {str(e)}")
            return []

    @staticmethod
    def _parse_max_speed(tracking_df):
        """Extract max speed from vsp tracking DataFrame classname field.
        Returns float mph or 0.0 if no speed found."""
        pattern = re.compile(r'^(\d+\.?\d*)\s+mph:')
        max_speed = 0.0
        for classname in tracking_df['classname']:
            m = pattern.match(str(classname))
            if m:
                speed = float(m.group(1))
                if speed > max_speed:
                    max_speed = speed
        return max_speed

    @staticmethod
    def _render_montage(viewname, feed, events, output_path, include_overlays,
                        header_frames, adaptive_text, logger):
        """Render montage video. Adapted from VideoExporter._render_video().
        Returns (frame_count, fps) tuple."""
        try:
            all_frames_data = []
            sub_events = []
            date_index_cache = {}

            refsort = {'trk': 0, 'obj': 1, 'vsp': 2, 'fd1': 3, 'fr1': 4}

            for event_idx, (date, event, starttime, imgsize) in enumerate(events):
                try:
                    frametimes = feed.get_image_list(date, event)

                    tracking_data = []
                    if include_overlays:
                        try:
                            if date not in date_index_cache:
                                date_index_cache[date] = feed.get_date_index(date)
                            cwIndx = date_index_cache[date]
                            evtSets = cwIndx.loc[cwIndx['event'] == event]

                            if len(evtSets.index) > 0:
                                # geometry/overlay types only — skip non-rect types like `crp`
                                trkTypes = [t for t in evtSets['type'] if t in refsort]
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
                                    evtData['name'] = evtData.apply(
                                        lambda x: str(x['classname']).split(':')[0], axis=1
                                    )

                                    for frametime in frametimes:
                                        frame_data = tuple(
                                            (rec.name, rec.classname,
                                             rec.rect_x1, rec.rect_y1, rec.rect_x2, rec.rect_y2)
                                            for rec in evtData.loc[
                                                evtData['timestamp'] == frametime
                                            ].sort_values(
                                                by=['ref'], key=lambda x: x.map(refsort)
                                            ).itertuples()
                                        )
                                        tracking_data.append(frame_data)
                        except Exception as e:
                            logger.warning(f"Could not load tracking data for {event}: {str(e)}")
                            tracking_data = [() for _ in frametimes]
                    else:
                        tracking_data = [() for _ in frametimes]

                    sub_events.append((len(all_frames_data), frametimes[0], event))

                    for i, frametime in enumerate(frametimes):
                        all_frames_data.append((date, event, frametime, tracking_data[i], imgsize))

                except Exception as e:
                    logger.warning(f"Could not process event {event}: {str(e)}")

            if len(all_frames_data) == 0:
                logger.error("No frames to render")
                return 0, 10.0

            # Calculate FPS from consecutive frame intervals, skipping inter-event gaps
            if len(all_frames_data) > 1:
                intervals = []
                for i in range(1, len(all_frames_data)):
                    prev_time = all_frames_data[i-1][2]
                    curr_time = all_frames_data[i][2]
                    interval = (curr_time - prev_time).total_seconds()
                    if 0 < interval < 2.0:
                        intervals.append(interval)

                if intervals:
                    avg_interval = sum(intervals) / len(intervals)
                    fps = 1.0 / avg_interval if avg_interval > 0 else 10.0
                    fps = max(5.0, min(fps, 30.0))
                else:
                    fps = 10.0
            else:
                fps = 10.0

            logger.info(f"Rendering {len(all_frames_data)} frames at {fps:.2f} fps")

            frame_width, frame_height = all_frames_data[0][4]
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(output_path, fourcc, fps, (frame_width, frame_height))

            text_helper = TextHelper()

            # Pre-calculate adaptive header text colors for each sub-event
            header_colors = {}
            if adaptive_text:
                for sub_idx, _, _ in sub_events:
                    if sub_idx < len(all_frames_data):
                        s_date, s_event, s_frametime, _, s_imgsize = all_frames_data[sub_idx]
                        try:
                            jpeg = feed.get_image_jpg(s_date, s_event, s_frametime)
                            frame = simplejpeg.decode_jpeg(jpeg, colorspace='BGR')
                            header_colors[sub_idx] = SpeedMontage._calculate_header_text_color(frame)
                        except Exception:
                            header_colors[sub_idx] = (255, 255, 255)

            # Render frames
            frames_in_subevent = 0
            current_subevent_idx = 0
            subevent_start_idx, subevent_start_time, subevent_id = sub_events[0]

            for frame_idx, (date, event, frametime, tracking, imgsize) in enumerate(all_frames_data):
                # Advance to next sub-event boundary if needed
                if current_subevent_idx + 1 < len(sub_events):
                    next_start, _, _ = sub_events[current_subevent_idx + 1]
                    if frame_idx >= next_start:
                        current_subevent_idx += 1
                        subevent_start_idx, subevent_start_time, subevent_id = sub_events[current_subevent_idx]
                        frames_in_subevent = 0

                try:
                    jpeg = feed.get_image_jpg(date, event, frametime)
                    frame = simplejpeg.decode_jpeg(jpeg, colorspace='BGR')

                    # Draw tracking overlays
                    if include_overlays and len(tracking) > 0:
                        names = list(set(name for name, _, _, _, _, _ in tracking))
                        text_helper.setColors(names)
                        for name, classname, x1, y1, x2, y2 in tracking:
                            text_helper.putText(frame, name, str(classname), x1, y1, x2, y2)

                    # Draw sub-event header banner for first N frames
                    if frames_in_subevent < header_frames:
                        header_text = (
                            f"{viewname} "
                            f"{subevent_start_time.strftime('%I:%M %p - %A %B %d, %Y')}"
                        )
                        text_color = (
                            header_colors.get(subevent_start_idx, (255, 255, 255))
                            if adaptive_text else (255, 255, 255)
                        )
                        (text_width, text_height), baseline = cv2.getTextSize(
                            header_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1
                        )
                        overlay = frame.copy()
                        cv2.rectangle(overlay, (15, 20), (30 + text_width, 50 + baseline), (0, 0, 0), -1)
                        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
                        cv2.putText(frame, header_text, (20, 40),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color, 1)

                    writer.write(frame)
                    frames_in_subevent += 1

                except Exception as e:
                    logger.warning(f"Could not render frame {frame_idx}: {str(e)}")

            writer.release()
            logger.info(f"Montage rendered: {len(all_frames_data)} frames")
            return len(all_frames_data), fps

        except Exception as e:
            logger.exception(f"Error rendering montage: {str(e)}")
            return 0, 10.0

    @staticmethod
    def _calculate_header_text_color(frame):
        """Calculate optimal text color based on frame brightness in the header region."""
        region = frame[0:min(50, frame.shape[0]), :]
        avg_bgr = np.mean(region, axis=(0, 1))
        luminance = ((avg_bgr[0] * 0.114) + (avg_bgr[1] * 0.587) + (avg_bgr[2] * 0.299)) / 255
        return (0, 0, 0) if luminance > 0.5 else (255, 255, 255)

    @staticmethod
    def _optimize_video(local_path, logger):
        """ffmpeg re-encode for web streaming with faststart."""
        temp_path = local_path[:-4] + "_ffmpeg.mp4"
        try:
            subprocess.run([
                'ffmpeg', '-i', local_path,
                '-c:v', 'libx264', '-profile:v', 'baseline', '-level', '3.0',
                '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                '-y', temp_path
            ], check=True, capture_output=True)
            os.replace(temp_path, local_path)
            logger.info("Video optimized for streaming")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Could not optimize video: {e.stderr.decode()}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception as e:
            logger.warning(f"Could not optimize video: {str(e)}")
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @staticmethod
    def _deliver(local_path, filename, viewname, date, cutoff,
                 speed_event_count, peak_speed, frame_count, fps, config, logger):
        """Transfer to datasink, optional VPS upload + notification."""
        output_cfg = config.get('output', {})
        output_dir = output_cfg.get('output_dir', 'datasink:/tmp/videos')
        vps_config = config.get('vps_upload', {})
        vps_enabled = vps_config.get('enabled', False)
        notif_config = config.get('notifications', {})
        site_description = config.get('site_description', 'SentinelCam')

        try:
            remote_path = f"{output_dir}/{filename}"
            result = subprocess.run(
                ['scp', local_path, remote_path],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                logger.info(f"Transferred {filename} to {output_dir}")
            else:
                logger.error(f"SCP transfer failed: {result.stderr}")
                return
        except subprocess.TimeoutExpired:
            logger.error("SCP transfer timed out")
            return
        except Exception as e:
            logger.exception(f"Transfer error: {str(e)}")
            return

        if vps_enabled:
            vps_success = SpeedMontage._upload_to_vps(local_path, filename, vps_config, logger)
            if vps_success and notif_config:
                SpeedMontage._send_notification(
                    filename, viewname, date, cutoff,
                    speed_event_count, peak_speed, frame_count, fps,
                    vps_config, notif_config, site_description, logger
                )

        try:
            os.remove(local_path)
        except Exception:
            pass

    @staticmethod
    def _upload_to_vps(local_path, filename, vps_config, logger):
        """Upload video file to VPS for external sharing. Returns True if successful."""
        try:
            logger.info(f"Uploading {filename} to VPS")
            vps_host = vps_config['host']
            vps_user = vps_config.get('user', 'rocky')
            vps_port = vps_config.get('port', 22)
            vps_path = vps_config.get('path', '/var/www/sentinelcam_exports')
            remote_dest = f"{vps_user}@{vps_host}:{vps_path}/{filename}"
            result = subprocess.run(
                ['scp', '-P', str(vps_port), local_path, remote_dest],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                logger.info(f"Successfully uploaded {filename} to VPS")
                return True
            else:
                logger.error(f"VPS upload failed: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            logger.error("VPS upload timed out")
            return False
        except Exception as e:
            logger.exception(f"VPS upload error: {str(e)}")
            return False

    @staticmethod
    def _send_notification(filename, viewname, date, cutoff, speed_event_count, peak_speed,
                           frame_count, fps, vps_config, notif_config, site_description, logger):
        """Send Telegram notification with secure link to montage video."""
        try:
            base_url = vps_config.get('base_url', 'https://yourvps.com/exports')
            secret = vps_config.get('secure_link_secret')
            expiry_hours = vps_config.get('link_expiry_hours', 72)

            expires = int(time.time()) + (expiry_hours * 3600)
            uri_path = f"/sentinelcam_exports/{filename}"

            md5_input = f"{expires}{uri_path}{secret}"
            md5_binary = hashlib.md5(md5_input.encode()).digest()
            md5_base64 = urlsafe_b64encode(md5_binary).decode().rstrip('=')
            secure_url = f"{base_url}/{filename}?md5={md5_base64}&expires={expires}"

            duration_sec = frame_count / fps if fps > 0 else 0
            message = (
                f"\U0001f697 *{site_description} Speed Montage*\n\n"
                f"\U0001f4f9 View: `{viewname}`\n"
                f"\U0001f5d3 Date: {date}\n"
                f"\u26a1 Events: {speed_event_count} above {cutoff:.0f} mph\n"
                f"\U0001f3ce Peak: {peak_speed:.1f} mph\n"
                f"\u23f1 Duration: {duration_sec:.0f}s\n"
                f"\u23f0 Link expires: {datetime.fromtimestamp(expires).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"[Download Video]({secure_url})"
            )

            telegram_config = notif_config.get('telegram', {})
            bot_token = telegram_config.get('bot_token')
            chat_id = telegram_config.get('chat_id')

            if not bot_token or not chat_id:
                logger.warning("Incomplete Telegram configuration, skipping notification")
                return

            SpeedMontage._send_telegram_message(bot_token, chat_id, message, logger)
            logger.info("Sent Telegram notification")

        except Exception as e:
            logger.exception(f"Notification error: {str(e)}")

    @staticmethod
    def _send_telegram_message(bot_token, chat_id, message, logger):
        """Send message via Telegram Bot API."""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'Markdown',
                'disable_web_page_preview': False
            }
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                logger.info(f"Telegram message sent to chat {chat_id}")
            else:
                logger.warning(f"Unexpected Telegram response: {response.status_code}")
        except Exception as e:
            logger.exception(f"Telegram send error: {str(e)}")


def main():
    """CLI entry point: parse args, load config, run montage."""
    parser = argparse.ArgumentParser(description='Generate daily speed event montage video')
    parser.add_argument('--config', required=True, help='Path to speed_montage.yaml configuration')
    parser.add_argument('--date', help='Target date YYYY-MM-DD (default: yesterday)')
    parser.add_argument('--cutoff', type=float, help='Speed cutoff override in mph')
    args = parser.parse_args()

    config = readConfig(args.config)
    target_date = args.date if args.date else (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s'
    )

    montage = SpeedMontage(config)
    montage.run(target_date, cutoff_override=args.cutoff)


if __name__ == '__main__':
    main()

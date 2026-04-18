"""System health display page for Sentinelcam Watchtower.

Displays outpost status cards, pipeline service cards, an alert feed,
and ramrod liveness indicator. All data comes from SYSHEALTH reports
delivered by ramrod via sentinel log re-broadcast.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import logging
import tkinter as tk
from collections import deque
from datetime import datetime

import PIL.Image
import PIL.ImageTk

logger = logging.getLogger("watchtower.health")

# Display geometry (800×480 7" touchscreen)
DISPLAY_W = 800
DISPLAY_H = 480

# Color scheme — consistent with storage_page.py
COLOR_BG = 'black'
COLOR_TEXT = 'white'
COLOR_MUTED = 'gray60'
COLOR_GREEN = '#2ECC71'
COLOR_YELLOW = '#F39C12'
COLOR_RED = '#E74C3C'
COLOR_BLUE = '#3498DB'

# Default thresholds (overridden by watchtower.yaml health_display section)
DEFAULT_SYSHEALTH_WARNING_AGE = 360    # seconds (6 min)
DEFAULT_SYSHEALTH_CRITICAL_AGE = 720   # seconds (12 min)
DEFAULT_FPS_WARNING = 20
DEFAULT_FPS_CRITICAL = 10
DEFAULT_MAX_ALERTS = 20

# Layout constants
OUTPOST_CARD_Y = 70
PIPELINE_CARD_Y = 230
ALERT_FEED_Y = 390


def _age_text(timestamp_str):
    """Format a SYSHEALTH timestamp into 'HH:MM (Nm ago)' with age in seconds."""
    try:
        ts = datetime.fromisoformat(timestamp_str)
        age = (datetime.now() - ts).total_seconds()
        time_str = ts.strftime('%H:%M')
        if age < 60:
            return time_str, f"{int(age)}s ago", age
        elif age < 3600:
            return time_str, f"{int(age / 60)}m ago", age
        else:
            return time_str, f"{int(age / 3600)}h ago", age
    except (ValueError, TypeError):
        return "—", "unknown", 9999


def _age_color(age_seconds, warning=DEFAULT_SYSHEALTH_WARNING_AGE,
               critical=DEFAULT_SYSHEALTH_CRITICAL_AGE):
    """Return color based on SYSHEALTH report age."""
    if age_seconds < warning:
        return COLOR_TEXT
    elif age_seconds < critical:
        return COLOR_YELLOW
    else:
        return COLOR_RED


def _status_color(ok):
    """Return indicator dot color from ok flag."""
    if ok is True:
        return COLOR_GREEN
    elif ok is False:
        return COLOR_RED
    else:
        return COLOR_MUTED  # None / unknown


def _fps_color(fps, warning=DEFAULT_FPS_WARNING, critical=DEFAULT_FPS_CRITICAL):
    """Return color for FPS display."""
    if fps is None:
        return COLOR_MUTED
    if fps >= warning:
        return COLOR_GREEN
    elif fps >= critical:
        return COLOR_YELLOW
    else:
        return COLOR_RED


def _capacity_color(pct):
    """Return color based on disk usage percentage."""
    if pct is None:
        return COLOR_MUTED
    if pct < 70:
        return COLOR_GREEN
    elif pct < 85:
        return COLOR_YELLOW
    else:
        return COLOR_RED


class SystemHealthPage(tk.Canvas):
    """System health dashboard driven by SYSHEALTH reports.

    Renders outpost status cards, pipeline service cards, and an alert
    feed. Updated when new SYSHEALTH reports arrive via the sentinel
    log subscriber.
    """

    def __init__(self, app, config=None):
        """
        Parameters
        ----------
        app : Application
            The Watchtower Application instance.
        config : dict, optional
            health_display config section from watchtower.yaml.
        """
        tk.Canvas.__init__(self, width=DISPLAY_W, height=DISPLAY_H,
                           borderwidth=0, highlightthickness=0,
                           background=COLOR_BG)
        self.app = app
        self._report = None
        self._alerts = deque(maxlen=(config or {}).get(
            'max_alerts', DEFAULT_MAX_ALERTS))
        self._age_timer = None

        # Configurable thresholds
        cfg = config or {}
        self._warning_age = cfg.get('syshealth_warning_age',
                                    DEFAULT_SYSHEALTH_WARNING_AGE)
        self._critical_age = cfg.get('syshealth_critical_age',
                                     DEFAULT_SYSHEALTH_CRITICAL_AGE)
        self._fps_warning = cfg.get('fps_warning', DEFAULT_FPS_WARNING)
        self._fps_critical = cfg.get('fps_critical', DEFAULT_FPS_CRITICAL)

        # Navigation images
        self.close_img = PIL.ImageTk.PhotoImage(file="images/close.png")

        # Draw static header elements
        self._draw_static_header()

    def _draw_static_header(self):
        """Draw the page title and close button."""
        self.create_text(30, 20, text="SYSTEM HEALTH", fill=COLOR_TEXT,
                         anchor='nw', font=('TkDefaultFont', 16, 'bold'),
                         tags='header')
        close_id = self.create_image(730, 10, anchor="nw",
                                     image=self.close_img, tags='header')
        self.tag_bind(close_id, "<Button-1>", lambda e: self._close())

    def _close(self):
        """Return to the player page."""
        from watchtower import UserPage
        self.app.show_page(UserPage.PLAYER)

    # ------------------------------------------------------------------
    #  Public interface
    # ------------------------------------------------------------------

    def refresh(self):
        """Called by show_page() when navigating to this page."""
        self._report = self.app._latest_syshealth
        self._render()
        self._start_age_timer()

    def on_syshealth(self, report):
        """Called when a new SYSHEALTH report arrives while page is visible."""
        self._report = report
        self._extract_alerts(report)
        self._render()

    def on_hide(self):
        """Called when navigating away from this page."""
        self._stop_age_timer()

    # ------------------------------------------------------------------
    #  Age timer — updates "last check" text every 30 seconds
    # ------------------------------------------------------------------

    def _start_age_timer(self):
        self._stop_age_timer()
        self._update_age()

    def _stop_age_timer(self):
        if self._age_timer is not None:
            self.after_cancel(self._age_timer)
            self._age_timer = None

    def _update_age(self):
        """Refresh just the 'last check' age text."""
        self.delete('age_text')
        if self._report:
            ts_str = self._report.get('timestamp', '')
            time_str, ago_str, age_sec = _age_text(ts_str)
            color = _age_color(age_sec, self._warning_age, self._critical_age)
            self.create_text(710, 20, text=f"Last check: {time_str} ({ago_str})",
                             fill=color, anchor='ne',
                             font=('TkDefaultFont', 10), tags='age_text')
        self._age_timer = self.after(30000, self._update_age)

    # ------------------------------------------------------------------
    #  Rendering
    # ------------------------------------------------------------------

    def _render(self):
        """Full page render from stored report."""
        self.delete('content')
        self.delete('age_text')

        if self._report is None:
            self.create_text(400, 240, text="No health data received",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 14),
                             tags='content')
            self.create_text(400, 270,
                             text="Waiting for SYSHEALTH report from ramrod",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 10),
                             tags='content')
            return

        # Update age display
        ts_str = self._report.get('timestamp', '')
        time_str, ago_str, age_sec = _age_text(ts_str)
        color = _age_color(age_sec, self._warning_age, self._critical_age)
        self.create_text(710, 20, text=f"Last check: {time_str} ({ago_str})",
                         fill=color, anchor='ne',
                         font=('TkDefaultFont', 10), tags='age_text')

        # Section label: OUTPOSTS
        self.create_text(30, OUTPOST_CARD_Y - 15, text="OUTPOSTS",
                         fill=COLOR_MUTED, anchor='nw',
                         font=('TkDefaultFont', 9), tags='content')

        # Outpost cards
        outposts = self._report.get('outposts', {})
        self._draw_outpost_cards(outposts, OUTPOST_CARD_Y)

        # Section label: PIPELINE
        self.create_text(30, PIPELINE_CARD_Y - 15, text="PIPELINE",
                         fill=COLOR_MUTED, anchor='nw',
                         font=('TkDefaultFont', 9), tags='content')

        # Pipeline cards
        self._draw_pipeline_cards(PIPELINE_CARD_Y)

        # Alert feed
        self._extract_alerts(self._report)
        self._draw_alert_feed(ALERT_FEED_Y)

    # ------------------------------------------------------------------
    #  Outpost cards
    # ------------------------------------------------------------------

    def _draw_outpost_cards(self, outposts, y_top):
        """Draw one status card per outpost, 3-across."""
        names = list(outposts.keys())
        if not names:
            self.create_text(400, y_top + 40, text="No outposts reported",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 11),
                             tags='content')
            return

        card_w = 220
        card_h = 120
        margin = 20
        # Center the cards horizontally
        total_w = len(names) * card_w + (len(names) - 1) * margin
        x_start = max(30, (DISPLAY_W - total_w) // 2)

        for i, name in enumerate(names):
            info = outposts[name]
            x = x_start + i * (card_w + margin)
            self._draw_outpost_card(name, info, x, y_top, card_w, card_h)

    def _draw_outpost_card(self, name, info, x, y, w, h):
        """Draw a single outpost status card."""
        ok = info.get('ok')
        is_down = ok is False
        stale = info.get('stale', False)

        # Card background
        outline = COLOR_RED if is_down else ('gray30' if stale else 'gray20')
        self.create_rectangle(x, y, x + w, y + h,
                              fill='gray10', outline=outline, width=2,
                              tags='content')

        # Status dot
        dot_color = _status_color(ok)
        self.create_oval(x + w - 25, y + 8, x + w - 10, y + 23,
                         fill=dot_color, outline='', tags='content')

        # Outpost name
        self.create_text(x + 12, y + 10, text=name, fill=COLOR_TEXT,
                         anchor='nw', font=('TkDefaultFont', 13, 'bold'),
                         tags='content')

        if is_down:
            self.create_text(x + w // 2, y + 55, text="DOWN",
                             fill=COLOR_RED, font=('TkDefaultFont', 16, 'bold'),
                             tags='content')
            if info.get('error'):
                self.create_text(x + w // 2, y + 80, text=info['error'],
                                 fill=COLOR_MUTED, font=('TkDefaultFont', 9),
                                 tags='content')
            return

        # FPS
        fps = info.get('fps')
        if fps is not None:
            fps_str = f"{fps:.1f} FPS"
            fps_clr = _fps_color(fps, self._fps_warning, self._fps_critical)
        else:
            fps_str = "— FPS"
            fps_clr = COLOR_MUTED
        self.create_text(x + 12, y + 35, text=fps_str, fill=fps_clr,
                         anchor='nw', font=('TkDefaultFont', 11),
                         tags='content')

        # Writer and heartbeat sub-indicators
        writer_alive = info.get('writer_alive')
        hb_age = info.get('heartbeat_age')

        # img indicator
        img_color = COLOR_GREEN if writer_alive else (
            COLOR_RED if writer_alive is False else COLOR_MUTED)
        self.create_oval(x + 12, y + 62, x + 22, y + 72,
                         fill=img_color, outline='', tags='content')
        self.create_text(x + 26, y + 62, text="img", fill=COLOR_MUTED,
                         anchor='nw', font=('TkDefaultFont', 9),
                         tags='content')

        # log (heartbeat) indicator
        if hb_age is not None and hb_age < 600:
            log_color = COLOR_GREEN
        elif hb_age is not None:
            log_color = COLOR_YELLOW
        elif info.get('last_heartbeat') is None:
            log_color = COLOR_MUTED
        else:
            log_color = COLOR_GREEN
        self.create_oval(x + 70, y + 62, x + 80, y + 72,
                         fill=log_color, outline='', tags='content')
        self.create_text(x + 84, y + 62, text="log", fill=COLOR_MUTED,
                         anchor='nw', font=('TkDefaultFont', 9),
                         tags='content')

        # Frames written
        frames = info.get('frames_written')
        if frames is not None:
            self.create_text(x + 12, y + 85, text=f"{frames:,} frames",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')

        # Stale warning
        if stale:
            self.create_text(x + w - 12, y + h - 12, text="STALE",
                             fill=COLOR_YELLOW, anchor='se',
                             font=('TkDefaultFont', 9, 'bold'),
                             tags='content')

    # ------------------------------------------------------------------
    #  Pipeline cards
    # ------------------------------------------------------------------

    def _draw_pipeline_cards(self, y_top):
        """Draw CamWatcher, DataPump, and Sentinel status cards."""
        card_w = 230
        card_h = 130
        margin = 20
        total_w = 3 * card_w + 2 * margin
        x_start = (DISPLAY_W - total_w) // 2

        x = x_start
        self._draw_camwatcher_card(x, y_top, card_w, card_h)
        x += card_w + margin
        self._draw_datapump_card(x, y_top, card_w, card_h)
        x += card_w + margin
        self._draw_sentinel_card(x, y_top, card_w, card_h)

    def _draw_camwatcher_card(self, x, y, w, h):
        """CamWatcher pipeline card."""
        cw = self._report.get('camwatcher', {})
        ok = cw.get('ok')

        self.create_rectangle(x, y, x + w, y + h,
                              fill='gray10', outline='gray20', width=2,
                              tags='content')

        # Title + status dot
        self.create_text(x + 12, y + 10, text="CamWatcher", fill=COLOR_TEXT,
                         anchor='nw', font=('TkDefaultFont', 12, 'bold'),
                         tags='content')
        dot_color = _status_color(ok)
        self.create_oval(x + w - 25, y + 8, x + w - 10, y + 23,
                         fill=dot_color, outline='', tags='content')

        if not ok:
            self.create_text(x + w // 2, y + h // 2 + 10,
                             text="UNREACHABLE" if ok is False else "UNKNOWN",
                             fill=COLOR_RED if ok is False else COLOR_MUTED,
                             font=('TkDefaultFont', 12, 'bold'),
                             tags='content')
            return

        line_y = y + 35
        # Writers: count alive / total
        writers = cw.get('writers', {})
        alive_count = sum(1 for w in writers.values() if w.get('alive'))
        total_count = len(writers)
        self.create_text(x + 12, line_y, text=f"writers: {alive_count}/{total_count}",
                         fill=COLOR_TEXT, anchor='nw',
                         font=('TkDefaultFont', 10), tags='content')

        # Disk usage
        line_y += 22
        disk = cw.get('disk', {})
        pct = disk.get('percent')
        if pct is not None:
            disk_color = _capacity_color(pct)
            self.create_text(x + 12, line_y, text=f"disk: {pct:.0f}%",
                             fill=disk_color, anchor='nw',
                             font=('TkDefaultFont', 10), tags='content')
        else:
            self.create_text(x + 12, line_y, text="disk: —",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 10), tags='content')

        # Sentinel agent
        line_y += 22
        agent = cw.get('sentinel_agent', {})
        agent_ok = agent.get('alive')
        agent_text = "agent: connected" if agent_ok else "agent: disconnected"
        agent_color = COLOR_GREEN if agent_ok else (COLOR_MUTED if agent_ok is None else COLOR_RED)
        self.create_text(x + 12, line_y, text=agent_text,
                         fill=agent_color, anchor='nw',
                         font=('TkDefaultFont', 10), tags='content')

        # Events today
        line_y += 22
        events = cw.get('events_today')
        if events is not None:
            self.create_text(x + 12, line_y, text=f"{events} events today",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')

    def _draw_datapump_card(self, x, y, w, h):
        """DataPump pipeline card."""
        dp = self._report.get('datapump', {})
        ok = dp.get('ok')

        self.create_rectangle(x, y, x + w, y + h,
                              fill='gray10', outline='gray20', width=2,
                              tags='content')

        self.create_text(x + 12, y + 10, text="DataPump", fill=COLOR_TEXT,
                         anchor='nw', font=('TkDefaultFont', 12, 'bold'),
                         tags='content')
        dot_color = _status_color(ok)
        self.create_oval(x + w - 25, y + 8, x + w - 10, y + 23,
                         fill=dot_color, outline='', tags='content')

        if not ok:
            self.create_text(x + w // 2, y + h // 2 + 10,
                             text="UNREACHABLE" if ok is False else "UNKNOWN",
                             fill=COLOR_RED if ok is False else COLOR_MUTED,
                             font=('TkDefaultFont', 12, 'bold'),
                             tags='content')
            return

        line_y = y + 35
        # Response time
        avg_ms = dp.get('avg_response_ms')
        if avg_ms is not None:
            self.create_text(x + 12, line_y, text=f"{avg_ms:.1f}ms resp",
                             fill=COLOR_TEXT, anchor='nw',
                             font=('TkDefaultFont', 10), tags='content')
        line_y += 22

        # Requests served
        reqs = dp.get('requests_served')
        if reqs is not None:
            self.create_text(x + 12, line_y, text=f"{reqs:,} requests",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 10), tags='content')
        line_y += 22

        # Uptime
        uptime = dp.get('uptime')
        if uptime:
            self.create_text(x + 12, line_y, text=f"up {uptime}",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')

    def _draw_sentinel_card(self, x, y, w, h):
        """Sentinel pipeline card — also a touch target for Phase 7 drill-down."""
        sn = self._report.get('sentinel', {})
        ok = sn.get('ok')

        self.create_rectangle(x, y, x + w, y + h,
                              fill='gray10', outline='gray20', width=2,
                              tags=('content', 'sentinel_card'))

        self.create_text(x + 12, y + 10, text="Sentinel", fill=COLOR_TEXT,
                         anchor='nw', font=('TkDefaultFont', 12, 'bold'),
                         tags=('content', 'sentinel_card'))
        dot_color = _status_color(ok)
        self.create_oval(x + w - 25, y + 8, x + w - 10, y + 23,
                         fill=dot_color, outline='',
                         tags=('content', 'sentinel_card'))

        if not ok:
            self.create_text(x + w // 2, y + h // 2 + 10,
                             text="UNREACHABLE" if ok is False else "UNKNOWN",
                             fill=COLOR_RED if ok is False else COLOR_MUTED,
                             font=('TkDefaultFont', 12, 'bold'),
                             tags=('content', 'sentinel_card'))
            return

        line_y = y + 35
        # Engine count
        engines = sn.get('engines', {})
        alive_count = sum(1 for e in engines.values() if e.get('alive'))
        total_count = len(engines)
        self.create_text(x + 12, line_y,
                         text=f"engines: {alive_count}/{total_count}",
                         fill=COLOR_TEXT, anchor='nw',
                         font=('TkDefaultFont', 10),
                         tags=('content', 'sentinel_card'))

        # Queue depth
        line_y += 22
        q = sn.get('queue', {})
        queued = q.get('queued', 0)
        running = q.get('running', 0)
        self.create_text(x + 12, line_y,
                         text=f"queue: {queued}  running: {running}",
                         fill=COLOR_TEXT, anchor='nw',
                         font=('TkDefaultFont', 10),
                         tags=('content', 'sentinel_card'))

        # Lead engine with FPS
        line_y += 22
        utilization = sn.get('utilization_pct', {})
        if engines:
            # Find most active engine (highest job count)
            lead_name = max(engines, key=lambda n: engines[n].get('job_count', 0))
            lead = engines[lead_name]
            fps = lead.get('avg_fps')
            fps_str = f"{fps:.1f} FPS" if fps else "— FPS"
            self.create_text(x + 12, line_y,
                             text=f"{lead_name}: {fps_str}",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 10),
                             tags=('content', 'sentinel_card'))

        # Uptime
        line_y += 22
        uptime = sn.get('uptime')
        if uptime:
            self.create_text(x + 12, line_y, text=f"up {uptime}",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9),
                             tags=('content', 'sentinel_card'))

        # Touch target for Phase 7 drill-down (stub)
        tap_id = self.create_rectangle(x, y, x + w, y + h,
                                       outline='', fill='',
                                       tags=('content', 'sentinel_tap'))
        self.tag_bind(tap_id, "<Button-1>", lambda e: self._sentinel_drilldown())

    def _sentinel_drilldown(self):
        """Navigate to sentinel performance drill-down."""
        from watchtower import UserPage
        self.app.show_page(UserPage.SENTINEL)

    # ------------------------------------------------------------------
    #  Alert feed
    # ------------------------------------------------------------------

    def _extract_alerts(self, report):
        """Derive alert entries from a SYSHEALTH report."""
        ts_str = report.get('timestamp', '')
        try:
            ts = datetime.fromisoformat(ts_str)
            ts_short = ts.strftime('%H:%M')
        except (ValueError, TypeError):
            ts_short = '??:??'

        # Outpost alerts
        for name, info in report.get('outposts', {}).items():
            if info.get('ok') is False:
                if info.get('stale'):
                    age = info.get('heartbeat_age', '?')
                    self._alerts.appendleft(
                        (ts_short, f"{name} heartbeat stale ({age}s)"))
                elif info.get('error'):
                    self._alerts.appendleft(
                        (ts_short, f"{name} — {info['error']}"))
                else:
                    self._alerts.appendleft(
                        (ts_short, f"{name} unreachable"))

        # Service alerts
        for svc in ('camwatcher', 'datapump', 'sentinel'):
            svc_data = report.get(svc, {})
            if svc_data.get('ok') is False:
                err = svc_data.get('error', 'no response')
                self._alerts.appendleft(
                    (ts_short, f"{svc} down — {err}"))

        # Sentinel engine health alerts
        sentinel = report.get('sentinel', {})
        if sentinel.get('ok'):
            for ename, edata in sentinel.get('engines', {}).items():
                health = edata.get('health', {})
                restarts = health.get('total_restarts', 0)
                failures = health.get('consecutive_failures', 0)
                if failures > 0:
                    self._alerts.appendleft(
                        (ts_short, f"Engine {ename}: {failures} consecutive failures"))
                if restarts > 0:
                    self._alerts.appendleft(
                        (ts_short, f"Engine {ename}: {restarts} auto-restarts"))

    def _draw_alert_feed(self, y_top):
        """Draw the bottom alert feed section."""
        self.create_text(30, y_top - 15, text="ALERTS", fill=COLOR_MUTED,
                         anchor='nw', font=('TkDefaultFont', 9),
                         tags='content')

        self.create_rectangle(25, y_top, DISPLAY_W - 25, DISPLAY_H - 10,
                              fill='gray5', outline='gray20', width=1,
                              tags='content')

        if not self._alerts:
            self.create_text(400, y_top + 35, text="No alerts",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 10),
                             tags='content')
            return

        # Display newest 4 alerts
        line_y = y_top + 8
        for i, (ts, msg) in enumerate(self._alerts):
            if i >= 4:
                break
            self.create_text(35, line_y, text=ts, fill=COLOR_MUTED,
                             anchor='nw', font=('TkDefaultFont', 9),
                             tags='content')
            self.create_text(85, line_y, text=msg, fill=COLOR_TEXT,
                             anchor='nw', font=('TkDefaultFont', 9),
                             tags='content')
            line_y += 18

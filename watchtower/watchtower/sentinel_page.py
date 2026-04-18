"""Sentinel performance drill-down page for Sentinelcam Watchtower.

Three-panel display with real-time engine performance, historical
charts, and daily summary history. All data comes from JOB records
accumulated via the sentinel log subscriber, SYSHEALTH snapshots
from ramrod, and historical HEALTH summaries from DataPump.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import logging
import tkinter as tk
from collections import deque
from datetime import datetime, timedelta

from sentinelcam.datafeed import DataFeed

logger = logging.getLogger("watchtower.sentinel")

# Display geometry (800×480 7" touchscreen)
DISPLAY_W = 800
DISPLAY_H = 480

# Color scheme — consistent with health_page.py
COLOR_BG = 'black'
COLOR_TEXT = 'white'
COLOR_MUTED = 'gray60'
COLOR_GREEN = '#2ECC71'
COLOR_YELLOW = '#F39C12'
COLOR_RED = '#E74C3C'
COLOR_BLUE = '#3498DB'
COLOR_CYAN = '#1ABC9C'
COLOR_PURPLE = '#9B59B6'
COLOR_ORANGE = '#E67E22'

# Engine dot colors for charts (up to 6 engines)
ENGINE_COLORS = [COLOR_CYAN, COLOR_ORANGE, COLOR_PURPLE, COLOR_GREEN, COLOR_YELLOW, COLOR_RED]

# Layout constants
NAV_Y = 8
NAV_H = 32
CONTENT_Y = 50
CONTENT_H = DISPLAY_H - CONTENT_Y - 5


def _engine_color(idx):
    """Return a color for engine index."""
    return ENGINE_COLORS[idx % len(ENGINE_COLORS)]


def _status_dot_color(health_data):
    """Derive engine status dot color from health counters."""
    if not health_data:
        return COLOR_MUTED
    h = health_data.get('health', {})
    if h.get('consecutive_failures', 0) >= 3:
        return COLOR_RED
    if h.get('consecutive_low_frames', 0) >= 2:
        return COLOR_YELLOW
    if not health_data.get('alive', True):
        return COLOR_RED
    return COLOR_GREEN


def _format_elapsed(sec_str):
    """Format elapsed seconds string as MM:SS."""
    try:
        parts = str(sec_str).split(':')
        if len(parts) == 3:
            return f"{parts[0]}:{parts[1]}:{parts[2][:2]}"
        secs = float(sec_str)
        m, s = divmod(int(secs), 60)
        return f"{m:02d}:{s:02d}"
    except (ValueError, TypeError):
        return str(sec_str)[:8] if sec_str else "—"


class SentinelPerformancePage(tk.Canvas):
    """Sentinel performance drill-down with Live, Charts, and History panels.

    Data sources:
    - JOB records from sentinel log subscriber (real-time, continuous)
    - SYSHEALTH reports from ramrod (engine status snapshots, every 2-3 min)
    - HEALTH daily summaries from DataPump (historical, fetched on demand)
    """

    PANEL_LIVE = 'live'
    PANEL_CHARTS = 'charts'
    PANEL_HISTORY = 'history'

    def __init__(self, app, datapumps):
        """
        Parameters
        ----------
        app : Application
            The Watchtower Application instance.
        datapumps : dict
            Mapping of sink_name -> datapump URL from config.
        """
        tk.Canvas.__init__(self, width=DISPLAY_W, height=DISPLAY_H,
                           borderwidth=0, highlightthickness=0,
                           background=COLOR_BG)
        self.app = app
        self.datapumps = datapumps
        self.feeds = {}
        self._panel = self.PANEL_LIVE
        self._historical = {}  # {date_str: [records]}
        self._history_loaded = False
        self._history_load_time = None  # throttle reloads
        self._refresh_timer = None

        # Draw static navigation bar
        self._draw_nav_bar()

    def _get_feed(self, sink_name):
        """Get or create a DataFeed connection for a data sink."""
        if sink_name not in self.feeds:
            self.feeds[sink_name] = DataFeed(self.datapumps[sink_name])
        return self.feeds[sink_name]

    # ------------------------------------------------------------------
    #  Navigation bar
    # ------------------------------------------------------------------

    def _draw_nav_bar(self):
        """Draw the tab navigation bar at the top."""
        self.delete('nav')
        tabs = [
            (self.PANEL_LIVE, "Live", 30),
            (self.PANEL_CHARTS, "Charts", 130),
            (self.PANEL_HISTORY, "History", 250),
        ]
        for panel, label, x in tabs:
            color = COLOR_TEXT if panel == self._panel else COLOR_MUTED
            font = ('TkDefaultFont', 12, 'bold') if panel == self._panel else ('TkDefaultFont', 12)
            tid = self.create_text(x, NAV_Y + 10, text=label, fill=color,
                                   anchor='nw', font=font, tags='nav')
            self.tag_bind(tid, "<Button-1>",
                          lambda e, p=panel: self._switch_panel(p))

        # Underline active tab
        if self._panel == self.PANEL_LIVE:
            self.create_line(30, NAV_H + 5, 100, NAV_H + 5,
                             fill=COLOR_BLUE, width=2, tags='nav')
        elif self._panel == self.PANEL_CHARTS:
            self.create_line(130, NAV_H + 5, 220, NAV_H + 5,
                             fill=COLOR_BLUE, width=2, tags='nav')
        elif self._panel == self.PANEL_HISTORY:
            self.create_line(250, NAV_H + 5, 350, NAV_H + 5,
                             fill=COLOR_BLUE, width=2, tags='nav')

        # Title
        self.create_text(400, NAV_Y + 4, text="SENTINEL", fill=COLOR_TEXT,
                         anchor='n', font=('TkDefaultFont', 10),
                         tags='nav')

        # Back button → System Health
        back_id = self.create_text(DISPLAY_W - 30, NAV_Y + 10,
                                   text="← System", fill=COLOR_MUTED,
                                   anchor='ne', font=('TkDefaultFont', 11),
                                   tags='nav')
        self.tag_bind(back_id, "<Button-1>", lambda e: self._go_back())

    def _switch_panel(self, panel):
        """Switch to a different panel."""
        if panel != self._panel:
            self._panel = panel
            self._draw_nav_bar()
            if panel == self.PANEL_HISTORY:
                self._maybe_reload_historical()
            self._render_panel()

    def _go_back(self):
        """Return to the system health page."""
        from watchtower import UserPage
        self.app.show_page(UserPage.HEALTH)

    # ------------------------------------------------------------------
    #  Public interface
    # ------------------------------------------------------------------

    def refresh(self):
        """Called by show_page() when navigating to this page."""
        self._render_panel()
        self._start_refresh_timer()
        if not self._history_loaded:
            self._load_historical()
        elif self._panel == self.PANEL_HISTORY:
            self._maybe_reload_historical()

    def on_hide(self):
        """Called when navigating away from this page."""
        self._stop_refresh_timer()

    def on_job_update(self):
        """Called when new JOB records arrive while this page is visible."""
        if self._panel in (self.PANEL_LIVE, self.PANEL_CHARTS, self.PANEL_HISTORY):
            self._render_panel()

    def on_syshealth(self, report):
        """Called when a new SYSHEALTH report arrives while page is visible."""
        if self._panel == self.PANEL_HISTORY:
            self._maybe_reload_historical()
        if self._panel in (self.PANEL_LIVE, self.PANEL_HISTORY):
            self._render_panel()

    # ------------------------------------------------------------------
    #  Refresh timer — redraws live/charts panels periodically
    # ------------------------------------------------------------------

    def _start_refresh_timer(self):
        self._stop_refresh_timer()
        self._tick_refresh()

    def _stop_refresh_timer(self):
        if self._refresh_timer is not None:
            self.after_cancel(self._refresh_timer)
            self._refresh_timer = None

    def _tick_refresh(self):
        """Periodic refresh for time-sensitive displays."""
        if self._panel in (self.PANEL_LIVE, self.PANEL_CHARTS):
            self._render_panel()
        self._refresh_timer = self.after(5000, self._tick_refresh)

    # ------------------------------------------------------------------
    #  Historical data loading
    # ------------------------------------------------------------------

    def _load_historical(self):
        """Fetch daily HEALTH summaries from DataPump."""
        self._historical = {}
        for sink_name in self.datapumps:
            try:
                feed = self._get_feed(sink_name)
                for d in range(7):
                    date_str = (datetime.now() - timedelta(days=d)).strftime('%Y-%m-%d')
                    if date_str not in self._historical:
                        records = feed.get_health_summary(date_str)
                        if records:
                            self._historical[date_str] = records
            except Exception as e:
                logger.error(f"Failed to load health history from {sink_name}: {e}")
        self._history_loaded = True
        self._history_load_time = datetime.now()
        logger.info(f"Loaded health summaries for {len(self._historical)} dates")

    def _maybe_reload_historical(self):
        """Reload historical data if stale, or if the calendar day has changed."""
        if self._history_load_time is None:
            self._load_historical()
        elif datetime.now().date() != self._history_load_time.date():
            # A new day has started — yesterday's HEALTH summary is now available
            self._load_historical()
        elif (datetime.now() - self._history_load_time).total_seconds() > 600:
            self._load_historical()

    # ------------------------------------------------------------------
    #  Panel routing
    # ------------------------------------------------------------------

    def _render_panel(self):
        """Route to the appropriate panel renderer."""
        self.delete('content')
        if self._panel == self.PANEL_LIVE:
            self._render_live()
        elif self._panel == self.PANEL_CHARTS:
            self._render_charts()
        elif self._panel == self.PANEL_HISTORY:
            self._render_history()

    # ==================================================================
    #  PANEL 1: Live Status
    # ==================================================================

    def _render_live(self):
        """Render the live engine status panel."""
        syshealth = self.app._latest_syshealth
        sentinel_data = (syshealth or {}).get('sentinel', {})
        engines = sentinel_data.get('engines', {})
        queue_info = sentinel_data.get('queue', {})
        utilization = sentinel_data.get('utilization_pct', {})
        df = self.app._job_df

        # --- Left column: engine cards ---
        card_x = 15
        card_y = CONTENT_Y + 5
        card_w = 155
        card_h = 95
        card_gap = 8

        engine_names = sorted(engines.keys()) if engines else []
        for i, name in enumerate(engine_names):
            edata = engines[name]
            y = card_y + i * (card_h + card_gap)
            if y + card_h > DISPLAY_H - 5:
                break
            self._draw_engine_card(name, edata, utilization.get(name, 0),
                                   card_x, y, card_w, card_h, i)

        if not engine_names:
            self.create_text(card_x + card_w // 2, card_y + 40,
                             text="No engine\ndata", fill=COLOR_MUTED,
                             font=('TkDefaultFont', 10), justify='center',
                             tags='content')

        # --- Right column: queue + recent completions ---
        right_x = card_x + card_w + 20

        # Current activity / running task
        self._draw_current_activity(engines, right_x, CONTENT_Y + 5)

        # Queue status
        self._draw_queue_status(queue_info, sentinel_data, right_x, CONTENT_Y + 115)

        # Recent completions from JOB records
        self._draw_recent_completions(df, right_x, CONTENT_Y + 235)

    def _draw_engine_card(self, name, edata, util_pct, x, y, w, h, idx):
        """Draw a single engine status card."""
        outline = 'gray25' if edata.get('status') == 'idle' else COLOR_BLUE
        self.create_rectangle(x, y, x + w, y + h,
                              fill='gray10', outline=outline, width=2,
                              tags='content')

        # Status dot
        dot_color = _status_dot_color(edata)
        self.create_oval(x + w - 20, y + 6, x + w - 8, y + 18,
                         fill=dot_color, outline='', tags='content')

        # Engine name
        self.create_text(x + 8, y + 6, text=name, fill=COLOR_TEXT,
                         anchor='nw', font=('TkDefaultFont', 11, 'bold'),
                         tags='content')

        # FPS
        fps = edata.get('avg_fps', 0)
        fps_str = f"{fps:.1f} FPS" if fps else "— FPS"
        self.create_text(x + 8, y + 28, text=fps_str,
                         fill=COLOR_TEXT, anchor='nw',
                         font=('TkDefaultFont', 10), tags='content')

        # Utilization
        util_str = f"{util_pct:.0f}% util"
        self.create_text(x + w - 8, y + 28, text=util_str,
                         fill=COLOR_MUTED, anchor='ne',
                         font=('TkDefaultFont', 9), tags='content')

        # Job count
        jobs = edata.get('job_count', 0)
        self.create_text(x + 8, y + 48, text=f"{jobs} jobs",
                         fill=COLOR_MUTED, anchor='nw',
                         font=('TkDefaultFont', 9), tags='content')

        # Ring latency
        health = edata.get('health', {})
        ring_avg = health.get('ring_next_avg', 0)
        if ring_avg > 0:
            ring_ms = ring_avg * 1000
            ring_color = COLOR_GREEN if ring_ms < 1.0 else (COLOR_YELLOW if ring_ms < 5.0 else COLOR_RED)
            self.create_text(x + 8, y + 65, text=f"ring: {ring_ms:.2f}ms",
                             fill=ring_color, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')

        # Current task (if running)
        task = edata.get('current_task')
        if task:
            # Truncate long task names
            display_task = task[:16] + "…" if len(task) > 16 else task
            self.create_text(x + w - 8, y + 48, text=display_task,
                             fill=COLOR_BLUE, anchor='ne',
                             font=('TkDefaultFont', 8), tags='content')

        # Accelerator badge
        accel = edata.get('accelerator', '')
        if accel:
            self.create_text(x + w - 8, y + h - 8, text=accel,
                             fill='gray35', anchor='se',
                             font=('TkDefaultFont', 8), tags='content')

    def _draw_current_activity(self, engines, x, y):
        """Draw current running task details."""
        running = [(n, e) for n, e in engines.items() if e.get('status') == 'running']

        self.create_text(x, y, text="RUNNING", fill=COLOR_MUTED,
                         anchor='nw', font=('TkDefaultFont', 9),
                         tags='content')
        line_y = y + 18
        if not running:
            self.create_text(x + 10, line_y, text="All engines idle",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 10), tags='content')
            return

        for name, edata in running[:3]:
            task = edata.get('current_task', '?')
            health = edata.get('health', {})
            ring_avg = health.get('ring_next_avg', 0)
            ring_str = f"  ring: {ring_avg*1000:.2f}ms" if ring_avg > 0 else ""

            marker = "▶"
            self.create_text(x, line_y, text=marker, fill=COLOR_GREEN,
                             anchor='nw', font=('TkDefaultFont', 9),
                             tags='content')
            self.create_text(x + 16, line_y, text=f"{task}",
                             fill=COLOR_TEXT, anchor='nw',
                             font=('TkDefaultFont', 10), tags='content')
            self.create_text(x + 16, line_y + 17,
                             text=f"{name}{ring_str}",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')
            line_y += 38

    def _draw_queue_status(self, queue_info, sentinel_data, x, y):
        """Draw queue depth and breakdown."""
        self.create_text(x, y, text="QUEUE", fill=COLOR_MUTED,
                         anchor='nw', font=('TkDefaultFont', 9),
                         tags='content')

        queued = queue_info.get('queued', 0)
        running = queue_info.get('running', 0)
        line_y = y + 18

        if queued == 0 and running == 0:
            self.create_text(x + 10, line_y, text="Empty",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 10), tags='content')
            return

        text = f"{queued} pending"
        if running > 0:
            text += f"  ({running} running)"
        self.create_text(x + 10, line_y, text=text, fill=COLOR_TEXT,
                         anchor='nw', font=('TkDefaultFont', 10),
                         tags='content')

        # Per-class breakdown
        by_class = sentinel_data.get('queue_by_class', {})
        if by_class:
            line_y += 18
            cls_parts = [f"cls{k}:{v}" for k, v in sorted(by_class.items()) if v > 0]
            if cls_parts:
                self.create_text(x + 10, line_y, text="  ".join(cls_parts),
                                 fill=COLOR_MUTED, anchor='nw',
                                 font=('TkDefaultFont', 9), tags='content')

        # Submission rate
        subs = sentinel_data.get('submissions_5min', 0)
        if subs > 0:
            line_y += 18
            self.create_text(x + 10, line_y, text=f"{subs} submitted/5min",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')

        # HWM
        hwm = queue_info.get('high_water_mark', 0)
        if hwm > 0:
            line_y += 18
            self.create_text(x + 10, line_y, text=f"HWM: {hwm}",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')

    def _draw_recent_completions(self, df, x, y):
        """Draw recent completed jobs from JOB record DataFrame."""
        self.create_text(x, y, text="RECENT", fill=COLOR_MUTED,
                         anchor='nw', font=('TkDefaultFont', 9),
                         tags='content')

        line_y = y + 18
        if df is None or len(df) == 0:
            self.create_text(x + 10, line_y, text="No job records yet",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 10), tags='content')
            return

        # Show last 6 completed jobs, newest first
        recent = df.sort_values('ended', ascending=False).head(6)
        for _, row in recent.iterrows():
            if line_y + 18 > DISPLAY_H - 10:
                break
            task = str(row.get('task', '?'))
            node = str(row.get('node', ''))
            elapsed = row.get('elapsed', '')
            status = row.get('status', '')
            elapsed_str = _format_elapsed(elapsed)

            # Status marker
            marker = "✓" if status == 'Done' else "✗"
            marker_color = COLOR_GREEN if status == 'Done' else COLOR_RED
            self.create_text(x, line_y, text=marker, fill=marker_color,
                             anchor='nw', font=('TkDefaultFont', 9),
                             tags='content')

            display_task = task[:14] + "…" if len(task) > 14 else task
            self.create_text(x + 16, line_y,
                             text=f"{display_task}  {node}  {elapsed_str}",
                             fill=COLOR_TEXT, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')
            line_y += 18

    # ==================================================================
    #  PANEL 2: Performance Charts (Tk Canvas)
    # ==================================================================

    def _render_charts(self):
        """Render the performance charts panel."""
        df = self.app._job_df
        if df is None or len(df) == 0:
            self.create_text(400, 240, text="No job data collected yet",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 14),
                             tags='content')
            self.create_text(400, 270,
                             text="JOB records will appear as sentinel completes tasks",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 10),
                             tags='content')
            return

        # Build engine color mapping
        engine_names = sorted(df['engine'].dropna().unique().tolist())
        engine_color_map = {name: _engine_color(i) for i, name in enumerate(engine_names)}

        # Two-chart layout: top and bottom halves
        self._draw_fps_chart(df, engine_color_map, CONTENT_Y, 195)
        self._draw_ring_latency_chart(df, engine_color_map, CONTENT_Y + 210, 195)

        # Legend (bottom)
        self._draw_chart_legend(engine_color_map, DISPLAY_H - 20)

    def _draw_fps_chart(self, df, engine_colors, y_top, height):
        """Chart A: Engine processing rate (FPS) over time — scatter plot."""
        chart_x = 65
        chart_w = DISPLAY_W - chart_x - 30
        chart_y = y_top + 20
        chart_h = height - 30
        chart_bottom = chart_y + chart_h

        self.create_text(chart_x - 5, y_top + 5, text="Processing Rate (FPS)",
                         fill=COLOR_MUTED, anchor='nw',
                         font=('TkDefaultFont', 9), tags='content')

        # Axes
        self.create_line(chart_x, chart_y, chart_x, chart_bottom,
                         fill='gray30', tags='content')
        self.create_line(chart_x, chart_bottom, chart_x + chart_w, chart_bottom,
                         fill='gray30', tags='content')

        # Filter valid rows
        valid = df.dropna(subset=['ended', 'rate']).copy()
        valid = valid[valid['rate'] > 0]
        if len(valid) == 0:
            return

        # Time range (8h window)
        now = datetime.now()
        t_min = now - timedelta(hours=8)
        t_range = 8 * 3600  # seconds

        # Y range
        max_fps = max(valid['rate'].max() * 1.1, 1.0)

        # Y-axis labels
        for i in range(5):
            val = max_fps * i / 4
            y = chart_bottom - (chart_h * i / 4)
            self.create_text(chart_x - 4, y, text=f"{val:.0f}",
                             fill='gray40', anchor='e',
                             font=('TkDefaultFont', 7), tags='content')
            if i > 0:
                self.create_line(chart_x, y, chart_x + chart_w, y,
                                 fill='gray15', tags='content')

        # X-axis labels (hours)
        for h in range(9):
            t = t_min + timedelta(hours=h)
            px = chart_x + int(chart_w * h / 8)
            label = t.strftime('%H:%M')
            self.create_text(px, chart_bottom + 3, text=label,
                             fill='gray35', anchor='n',
                             font=('TkDefaultFont', 7), tags='content')

        # Plot dots
        for _, row in valid.iterrows():
            try:
                t = row['ended']
                if hasattr(t, 'timestamp'):
                    t_sec = (t - t_min).total_seconds()
                else:
                    continue
                if t_sec < 0 or t_sec > t_range:
                    continue
                px = chart_x + int(chart_w * t_sec / t_range)
                py = chart_bottom - int(chart_h * min(row['rate'], max_fps) / max_fps)
                color = engine_colors.get(row.get('engine', ''), COLOR_MUTED)
                self.create_oval(px - 2, py - 2, px + 2, py + 2,
                                 fill=color, outline='', tags='content')
            except (TypeError, ValueError):
                continue

    def _draw_ring_latency_chart(self, df, engine_colors, y_top, height):
        """Chart C: Ring buffer latency over time — scatter plot."""
        chart_x = 65
        chart_w = DISPLAY_W - chart_x - 30
        chart_y = y_top + 20
        chart_h = height - 30
        chart_bottom = chart_y + chart_h

        self.create_text(chart_x - 5, y_top + 5, text="Ring Latency (ms)",
                         fill=COLOR_MUTED, anchor='nw',
                         font=('TkDefaultFont', 9), tags='content')

        # Axes
        self.create_line(chart_x, chart_y, chart_x, chart_bottom,
                         fill='gray30', tags='content')
        self.create_line(chart_x, chart_bottom, chart_x + chart_w, chart_bottom,
                         fill='gray30', tags='content')

        # Filter valid rows — use ring_next_avg
        valid = df.dropna(subset=['ended']).copy()
        valid = valid[valid.get('ring_next_avg', 0) > 0] if 'ring_next_avg' in valid.columns else valid.iloc[0:0]
        if len(valid) == 0:
            self.create_text(chart_x + chart_w // 2, chart_y + chart_h // 2,
                             text="No ring latency data",
                             fill='gray25', font=('TkDefaultFont', 10),
                             tags='content')
            return

        # Convert to ms
        valid = valid.copy()
        valid['ring_ms'] = valid['ring_next_avg'] * 1000

        # Time range
        now = datetime.now()
        t_min = now - timedelta(hours=8)
        t_range = 8 * 3600

        # Y range — dynamic scaling based on data
        raw_max = valid['ring_ms'].max()
        max_ms = raw_max * 1.2
        # Round up to a clean tick value for readability
        if max_ms <= 1.0:
            max_ms = 1.0
        elif max_ms <= 5.0:
            max_ms = round(max_ms + 0.49, 0)  # ceil to next integer
        elif max_ms <= 20.0:
            max_ms = round(max_ms / 5 + 0.49) * 5  # ceil to next 5
        else:
            max_ms = round(max_ms / 10 + 0.49) * 10  # ceil to next 10

        # Y-axis labels
        for i in range(5):
            val = max_ms * i / 4
            y = chart_bottom - (chart_h * i / 4)
            fmt = f"{val:.1f}" if max_ms < 5 else f"{val:.0f}"
            self.create_text(chart_x - 4, y, text=fmt,
                             fill='gray40', anchor='e',
                             font=('TkDefaultFont', 7), tags='content')
            if i > 0:
                self.create_line(chart_x, y, chart_x + chart_w, y,
                                 fill='gray15', tags='content')

        # Baseline reference line at 0.5ms
        baseline_y = chart_bottom - int(chart_h * 0.5 / max_ms)
        if 0 < (chart_bottom - baseline_y) < chart_h:
            self.create_line(chart_x, baseline_y, chart_x + chart_w, baseline_y,
                             fill='gray25', dash=(4, 4), tags='content')
            self.create_text(chart_x + chart_w + 2, baseline_y,
                             text="baseline", fill='gray30', anchor='w',
                             font=('TkDefaultFont', 7), tags='content')

        # X-axis labels
        for h in range(9):
            t = t_min + timedelta(hours=h)
            px = chart_x + int(chart_w * h / 8)
            label = t.strftime('%H:%M')
            self.create_text(px, chart_bottom + 3, text=label,
                             fill='gray35', anchor='n',
                             font=('TkDefaultFont', 7), tags='content')

        # Plot dots
        for _, row in valid.iterrows():
            try:
                t = row['ended']
                if hasattr(t, 'timestamp'):
                    t_sec = (t - t_min).total_seconds()
                else:
                    continue
                if t_sec < 0 or t_sec > t_range:
                    continue
                px = chart_x + int(chart_w * t_sec / t_range)
                py = chart_bottom - int(chart_h * min(row['ring_ms'], max_ms) / max_ms)
                color = engine_colors.get(row.get('engine', ''), COLOR_MUTED)
                self.create_oval(px - 2, py - 2, px + 2, py + 2,
                                 fill=color, outline='', tags='content')
            except (TypeError, ValueError):
                continue

    def _draw_chart_legend(self, engine_colors, y):
        """Draw engine color legend at the bottom of the charts panel."""
        x = 65
        for name, color in engine_colors.items():
            self.create_oval(x, y - 5, x + 8, y + 3,
                             fill=color, outline='', tags='content')
            self.create_text(x + 12, y - 5, text=name, fill=COLOR_MUTED,
                             anchor='nw', font=('TkDefaultFont', 8),
                             tags='content')
            x += len(name) * 8 + 30

    # ==================================================================
    #  PANEL 3: History + Bottleneck Events
    # ==================================================================

    def _render_history(self):
        """Render the historical summary panel with bottleneck events."""
        has_history = bool(self._historical)
        has_live = self.app._job_df is not None and len(self.app._job_df) > 0
        if not has_history and not has_live:
            self.create_text(400, 240, text="No historical data available",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 14),
                             tags='content')
            self.create_text(400, 270,
                             text="HEALTH summaries arrive after daily maintenance runs",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 10),
                             tags='content')
            return

        # Create scrollable frame via canvas scrollregion
        # (For simplicity on Pi touchscreen, render what fits and indicate overflow)
        y = CONTENT_Y + 5

        # Daily summary cards — show last 3 days across the top
        y = self._draw_daily_cards(y)

        # 7-day trends below
        y = self._draw_weekly_trends(y + 15)

        # Bottleneck events at the bottom (scrollable section)
        self._draw_bottleneck_events(y + 15)

    @staticmethod
    def _merge_daily_records(records):
        """Merge multiple HEALTH records for a single date.

        Uses the record with the highest total_jobs as the base — later
        maintenance runs append degraded records as retention trims old
        jobs, so the last record is often the least complete. Takes the
        max across all records for job totals and HWM fields.
        """
        if not records:
            return {}
        # Pick the record with the most jobs as the base
        best_idx = 0
        best_jobs = 0
        for i, rec in enumerate(records):
            n = rec.get('system', {}).get('total_jobs', 0)
            if n >= best_jobs:
                best_jobs = n
                best_idx = i
        summary = dict(records[best_idx])
        system = dict(summary.get('system', {}))
        for rec in records:
            rsys = rec.get('system', {})
            for key in ('total_jobs', 'total_failed',
                        'queue_hwm', 'queue_latency_hwm_sec'):
                if rsys.get(key, 0) > system.get(key, 0):
                    system[key] = rsys[key]
            # Per-class HWM: take per-key max
            for cls, val in rsys.get('queue_by_class_hwm', {}).items():
                existing = system.get('queue_by_class_hwm', {})
                if val > existing.get(cls, 0):
                    if 'queue_by_class_hwm' not in system:
                        system['queue_by_class_hwm'] = {}
                    system['queue_by_class_hwm'][cls] = val
        summary['system'] = system
        return summary

    def _draw_daily_cards(self, y_top):
        """Draw daily summary cards, 3-across, from HEALTH records."""
        dates = sorted(self._historical.keys(), reverse=True)[:3]
        # Ensure today is included when we have live JOB data
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in dates and self.app._job_df is not None and len(self.app._job_df) > 0:
            dates = [today] + dates
            dates = dates[:3]
        if not dates:
            return y_top

        card_w = 240
        card_h = 155
        margin = 12
        total_w = len(dates) * card_w + (len(dates) - 1) * margin
        x_start = max(15, (DISPLAY_W - total_w) // 2)

        for i, date_str in enumerate(dates):
            records = self._historical.get(date_str, [])
            summary = self._merge_daily_records(records)
            x = x_start + i * (card_w + margin)
            self._draw_daily_card(date_str, summary, x, y_top, card_w, card_h)

        return y_top + card_h

    def _draw_daily_card(self, date_str, summary, x, y, w, h):
        """Draw a single daily summary card."""
        today = datetime.now().strftime('%Y-%m-%d')
        label = "Today" if date_str == today else date_str

        self.create_rectangle(x, y, x + w, y + h,
                              fill='gray10', outline='gray25', width=1,
                              tags='content')
        self.create_text(x + 8, y + 5, text=label, fill=COLOR_TEXT,
                         anchor='nw', font=('TkDefaultFont', 11, 'bold'),
                         tags='content')

        system = summary.get('system', {})
        engines_data = summary.get('engines', {})

        line_y = y + 28
        line_h = 17

        # For today, prefer live SYSHEALTH + JOB DataFrame over stale
        # health.json (which is only written at maintenance time)
        total_jobs = system.get('total_jobs', '—')
        total_failed = system.get('total_failed', 0)
        if date_str == today:
            # Live job counts from accumulated JOB records
            df = self.app._job_df
            if df is not None and len(df) > 0:
                today_df = df.loc[df['date'] == today] if 'date' in df.columns else df
                live_count = len(today_df)
                live_failed = len(today_df.loc[today_df['status'] != 'Done']) if 'status' in today_df.columns else 0
                if live_count > (total_jobs if isinstance(total_jobs, int) else 0):
                    total_jobs = live_count
                    total_failed = live_failed
            # Live queue HWMs and utilization from SYSHEALTH
            syshealth = self.app._latest_syshealth
            if syshealth:
                sentinel_data = syshealth.get('sentinel', {})
                live_queue = sentinel_data.get('queue', {})
                live_hwm = live_queue.get('high_water_mark', 0)
                if live_hwm > system.get('queue_hwm', 0):
                    system['queue_hwm'] = live_hwm
                live_lat_hwm = live_queue.get('latency_hwm_sec', 0)
                if live_lat_hwm > system.get('queue_latency_hwm_sec', 0):
                    system['queue_latency_hwm_sec'] = live_lat_hwm
                live_cls_hwm = sentinel_data.get('queue_by_class_hwm', {})
                for cls, val in live_cls_hwm.items():
                    existing = system.get('queue_by_class_hwm', {})
                    if val > existing.get(cls, 0):
                        if 'queue_by_class_hwm' not in system:
                            system['queue_by_class_hwm'] = {}
                        system['queue_by_class_hwm'][cls] = val
                # Live engine utilization
                live_util = sentinel_data.get('utilization_pct', {})
                live_engines = sentinel_data.get('engines', {})
                if live_util or live_engines:
                    engines_data = dict(engines_data)  # local copy to mutate
                    for ename in set(list(live_util.keys()) + list(live_engines.keys())):
                        if ename not in engines_data:
                            engines_data[ename] = {}
                        if ename in live_util:
                            engines_data[ename]['utilization_pct'] = live_util[ename]
                        einfo = live_engines.get(ename, {})
                        if einfo.get('ring_next_avg', 0) > 0:
                            engines_data[ename]['ring_next_avg'] = einfo['ring_next_avg']
        fail_color = COLOR_RED if total_failed and total_failed > 0 else COLOR_MUTED
        self.create_text(x + 8, line_y,
                         text=f"Jobs: {total_jobs}", fill=COLOR_TEXT,
                         anchor='nw', font=('TkDefaultFont', 9),
                         tags='content')
        if total_failed:
            self.create_text(x + w - 8, line_y,
                             text=f"Failed: {total_failed}", fill=fail_color,
                             anchor='ne', font=('TkDefaultFont', 9),
                             tags='content')

        # Queue HWM
        line_y += line_h
        q_hwm = system.get('queue_hwm', 0)
        self.create_text(x + 8, line_y,
                         text=f"Q HWM: {q_hwm}", fill=COLOR_MUTED,
                         anchor='nw', font=('TkDefaultFont', 9),
                         tags='content')
        # Per-class HWM
        cls_hwm = system.get('queue_by_class_hwm', {})
        if cls_hwm:
            cls_parts = [f"cls{k}:{v}" for k, v in sorted(cls_hwm.items()) if v]
            if cls_parts:
                self.create_text(x + w - 8, line_y,
                                 text="  ".join(cls_parts), fill='gray40',
                                 anchor='ne', font=('TkDefaultFont', 8),
                                 tags='content')

        # Latency HWM
        line_y += line_h
        lat_hwm = system.get('queue_latency_hwm_sec', 0)
        if lat_hwm:
            self.create_text(x + 8, line_y,
                             text=f"Latency HWM: {lat_hwm:.1f}s",
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')

        # Engine utilization
        line_y += line_h + 3
        self.create_text(x + 8, line_y, text="Utilization:",
                         fill='gray40', anchor='nw',
                         font=('TkDefaultFont', 8), tags='content')
        line_y += 14
        for ename, edata in engines_data.items():
            if line_y > y + h - 12:
                break
            util = edata.get('utilization_pct', 0)
            restarts = edata.get('restarts', 0)
            ring_avg = edata.get('ring_next_avg', 0)
            parts = [f"{ename}: {util:.0f}%"]
            if restarts:
                parts.append(f"↻{restarts}")
            if ring_avg > 0:
                parts.append(f"ring:{ring_avg*1000:.2f}ms")
            text = "  ".join(parts)
            color = COLOR_TEXT if util > 50 else COLOR_MUTED
            self.create_text(x + 12, line_y, text=text,
                             fill=color, anchor='nw',
                             font=('TkDefaultFont', 8), tags='content')
            line_y += 13

    def _draw_weekly_trends(self, y_top):
        """Draw 7-day aggregated trend data."""
        dates = sorted(self._historical.keys(), reverse=True)[:7]
        if len(dates) < 2:
            return y_top

        self.create_text(30, y_top, text="7-DAY TRENDS", fill=COLOR_MUTED,
                         anchor='nw', font=('TkDefaultFont', 9),
                         tags='content')
        line_y = y_top + 18

        # Aggregate failures and restarts
        total_failures = {}
        total_restarts = {}
        for date_str in dates:
            records = self._historical.get(date_str, [])
            if not records:
                continue
            summary = records[-1]
            for ename, edata in summary.get('engines', {}).items():
                total_restarts[ename] = total_restarts.get(ename, 0) + edata.get('restarts', 0)
            for tname, tdata in summary.get('tasks', {}).items():
                total_failures[tname] = total_failures.get(tname, 0) + tdata.get('failed', 0)

        # Show failures by task
        if any(v > 0 for v in total_failures.values()):
            fail_parts = [f"{t}: {c}" for t, c in sorted(total_failures.items(), key=lambda x: -x[1]) if c > 0]
            self.create_text(30, line_y, text="Failures: " + ", ".join(fail_parts[:4]),
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')
            line_y += 16

        # Show restarts by engine
        if any(v > 0 for v in total_restarts.values()):
            restart_parts = [f"{e}: {c}" for e, c in sorted(total_restarts.items()) if c > 0]
            self.create_text(30, line_y, text="Restarts: " + ", ".join(restart_parts),
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')
            line_y += 16

        if not any(v > 0 for v in total_failures.values()) and \
           not any(v > 0 for v in total_restarts.values()):
            self.create_text(30, line_y, text="No failures or restarts in 7 days",
                             fill='gray30', anchor='nw',
                             font=('TkDefaultFont', 9), tags='content')
            line_y += 16

        return line_y

    def _draw_bottleneck_events(self, y_top):
        """Draw HWM snapshot cards as scrollable bottleneck event section."""
        # Get snapshots from latest SYSHEALTH
        syshealth = self.app._latest_syshealth
        sentinel_data = (syshealth or {}).get('sentinel', {})
        depth_snapshots = sentinel_data.get('depth_hwm_snapshots', [])
        latency_snapshots = sentinel_data.get('latency_hwm_snapshots', [])

        all_snapshots = []
        for s in depth_snapshots:
            s = dict(s)  # avoid mutating the cached report
            s['_trigger_type'] = 'depth'
            all_snapshots.append(s)
        for s in latency_snapshots:
            s = dict(s)  # avoid mutating the cached report
            s['_trigger_type'] = 'latency'
            all_snapshots.append(s)

        if not all_snapshots:
            if y_top < DISPLAY_H - 30:
                self.create_text(30, y_top,
                                 text="BOTTLENECK EVENTS", fill=COLOR_MUTED,
                                 anchor='nw', font=('TkDefaultFont', 9),
                                 tags='content')
                self.create_text(30, y_top + 18,
                                 text="No queue peaks recorded this period",
                                 fill='gray30', anchor='nw',
                                 font=('TkDefaultFont', 9), tags='content')
            return

        self.create_text(30, y_top, text="BOTTLENECK EVENTS",
                         fill=COLOR_MUTED, anchor='nw',
                         font=('TkDefaultFont', 9), tags='content')

        # Sort by timestamp, newest first
        all_snapshots.sort(key=lambda s: s.get('timestamp', ''), reverse=True)

        # Deduplicate: collapse snapshots within the same minute and
        # trigger type, keeping the one with the highest depth or latency
        deduped = []
        seen = set()
        for snap in all_snapshots:
            ts = snap.get('timestamp', '')
            try:
                ts_minute = datetime.fromisoformat(ts).strftime('%H:%M')
            except (ValueError, TypeError):
                ts_minute = ts[:16]  # fallback rough grouping
            key = (ts_minute, snap.get('_trigger_type', ''))
            if key not in seen:
                seen.add(key)
                deduped.append(snap)
        all_snapshots = deduped

        line_y = y_top + 18
        for snap in all_snapshots[:4]:
            if line_y + 40 > DISPLAY_H - 5:
                break
            trigger = snap.get('_trigger_type', '?')
            ts = snap.get('timestamp', '')
            try:
                ts_short = datetime.fromisoformat(ts).strftime('%H:%M')
            except (ValueError, TypeError):
                ts_short = '??:??'
            depth = snap.get('queue_depth', 0)
            latency = snap.get('queue_latency_sec', 0)
            subs = snap.get('submissions_5min', 0)
            by_class = snap.get('by_class', {})
            by_task = snap.get('by_task', {})

            # Trigger label
            trigger_color = COLOR_YELLOW if trigger == 'depth' else COLOR_ORANGE
            self.create_text(30, line_y,
                             text=f"{ts_short}  {trigger} peak",
                             fill=trigger_color, anchor='nw',
                             font=('TkDefaultFont', 9, 'bold'),
                             tags='content')

            # Details
            detail_parts = [f"depth:{depth}"]
            if latency:
                detail_parts.append(f"lat:{latency:.0f}s")
            if subs:
                detail_parts.append(f"subs:{subs}/5m")
            cls_parts = [f"c{k}:{v}" for k, v in sorted(by_class.items()) if v]
            if cls_parts:
                detail_parts.extend(cls_parts)

            self.create_text(30, line_y + 15,
                             text="  ".join(detail_parts),
                             fill=COLOR_MUTED, anchor='nw',
                             font=('TkDefaultFont', 8), tags='content')

            # Task breakdown
            if by_task:
                task_parts = [f"{t}×{c}" for t, c in sorted(by_task.items(), key=lambda x: -x[1])[:3]]
                self.create_text(30, line_y + 28,
                                 text="  ".join(task_parts),
                                 fill='gray35', anchor='nw',
                                 font=('TkDefaultFont', 8), tags='content')

            line_y += 45

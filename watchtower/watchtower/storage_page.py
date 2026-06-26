"""Storage analysis report page for Sentinelcam Watchtower

Displays disk capacity, runway estimates, daily intake trends, and
per-view storage breakdowns for each configured data sink. All data
comes from pre-computed storage reports served by DataPump.

Level 1: Sink overview — capacity gauge, runway estimate, coverage summary
Level 2: Daily intake trend — bar chart (pure Tkinter Canvas)
Level 3: Per-view breakdown — horizontal bars showing camera storage share

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import logging
import tkinter as tk
from datetime import datetime, timedelta

import numpy as np
import PIL.Image, PIL.ImageTk

from sentinelcam.datafeed import DataFeed

logger = logging.getLogger("watchtower.storage")

# Display geometry (800×480 7" touchscreen)
DISPLAY_W = 800
DISPLAY_H = 480

# Color scheme
COLOR_BG = 'black'
COLOR_TEXT = 'white'
COLOR_MUTED = 'gray60'
COLOR_GREEN = '#2ECC71'
COLOR_YELLOW = '#F39C12'
COLOR_RED = '#E74C3C'
COLOR_BLUE = '#3498DB'
COLOR_BAR = '#2E86AB'
COLOR_BAR_ALT = '#A23B72'


def format_bytes(b, decimals=1):
    """Format byte count as human-readable string."""
    if b < 1024:
        return f"{b} B"
    elif b < 1024**2:
        return f"{b / 1024:.{decimals}f} KB"
    elif b < 1024**3:
        return f"{b / 1024**2:.{decimals}f} MB"
    else:
        return f"{b / 1024**3:.{decimals}f} GB"


def capacity_color(pct):
    """Return color based on disk usage percentage."""
    if pct < 70:
        return COLOR_GREEN
    elif pct < 85:
        return COLOR_YELLOW
    else:
        return COLOR_RED


class StoragePage(tk.Canvas):
    """Multi-level storage report display.

    Level 1 (overview): Capacity gauge and runway for each data sink.
    Level 2 (daily): Bar chart of daily image intake.
    Level 3 (per-view): Per-camera storage breakdown for a date range.
    """

    def __init__(self, app, datapumps):
        """
        Parameters
        ----------
        app : Application
            The Watchtower Application instance
        datapumps : dict
            Mapping of sink_name -> datapump URL from config
        """
        tk.Canvas.__init__(self, width=DISPLAY_W, height=DISPLAY_H,
                           borderwidth=0, highlightthickness=0,
                           background=COLOR_BG)
        self.app = app
        self.datapumps = datapumps  # {name: url}
        self.reports = {}           # {sink_name: report_dict}
        self.feeds = {}             # {sink_name: DataFeed}  (lazy)
        self.level = 1
        self.selected_sink = None
        self.selected_dates = None

        # Navigation images
        self.close_img = PIL.ImageTk.PhotoImage(file="images/close.png")
        self.back_img = PIL.ImageTk.PhotoImage(file="images/close.png")  # reuse close icon for back

        # Draw static frame
        self._draw_header()

    # ------------------------------------------------------------------
    #  Navigation
    # ------------------------------------------------------------------

    def _draw_header(self):
        """Draw the page header with title and close button."""
        self.delete('header')
        title = "Storage Report"
        if self.level == 2 and self.selected_sink:
            title = f"Daily Intake — {self.selected_sink}"
        elif self.level == 3 and self.selected_sink:
            title = f"Per-View — {self.selected_sink}"
        self.create_text(400, 20, text=title, fill=COLOR_TEXT, anchor='n',
                         font=('TkDefaultFont', 16, 'bold'), tags='header')

        # Close / Back button (top-right)
        close_id = self.create_image(730, 10, anchor="nw", image=self.close_img,
                                     tags='header')
        if self.level == 1:
            self.tag_bind(close_id, "<Button-1>", lambda e: self._close())
        else:
            self.tag_bind(close_id, "<Button-1>", lambda e: self._go_back())

    def _close(self):
        """Return to the player page."""
        from watchtower import UserPage
        self.app.show_page(UserPage.SETTINGS)

    def _go_back(self):
        """Navigate back one level."""
        if self.level == 3:
            self.level = 2
            self._show_daily_trend()
        elif self.level == 2:
            self.level = 1
            self._show_overview()

    def refresh(self):
        """Fetch reports from all data sinks and display Level 1."""
        self.reports = {}
        for sink_name, url in self.datapumps.items():
            try:
                feed = self._get_feed(sink_name, url)
                report = feed.get_storage_report()
                if report:
                    self.reports[sink_name] = report
                    logger.info(f"Loaded storage report from {sink_name}")
                else:
                    logger.warning(f"No storage report available from {sink_name}")
            except Exception as e:
                logger.error(f"Failed to fetch storage report from {sink_name}: {e}")
        self.level = 1
        self.selected_sink = None
        self._show_overview()

    def _get_feed(self, sink_name, url):
        """Get or create a DataFeed connection for a data sink."""
        if sink_name not in self.feeds:
            self.feeds[sink_name] = DataFeed(url)
        return self.feeds[sink_name]

    # ------------------------------------------------------------------
    #  Level 1 — Sink Overview
    # ------------------------------------------------------------------

    def _show_overview(self):
        """Render Level 1: capacity gauge and summary for each sink."""
        self.delete('content')
        self._draw_header()

        if not self.reports:
            self.create_text(400, 240, text="No storage reports available",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 14),
                             tags='content')
            self.create_text(400, 270,
                             text="Reports are generated nightly by the storage analysis job",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 10),
                             tags='content')
            return

        # Layout: stack sinks vertically starting at y=55
        y_offset = 55
        panel_height = min(380 // max(len(self.reports), 1), 380)

        for sink_name, report in self.reports.items():
            self._draw_sink_panel(sink_name, report, y_offset, panel_height)
            y_offset += panel_height

    def _draw_sink_panel(self, sink_name, report, y_top, height):
        """Draw a single data sink overview panel."""
        disk = report.get('disk_summary')
        daily = report.get('daily_summary')
        generated = report.get('generated_at')

        if disk is None or len(disk) == 0:
            self.create_text(400, y_top + 30, text=f"{sink_name}: No disk data",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 12),
                             tags='content')
            return

        # Use the most recent disk_summary row
        latest = disk.iloc[-1]
        total = latest['total_bytes']
        used = latest['used_bytes']
        free = latest['free_bytes']
        pct_used = (used / total * 100) if total > 0 else 0

        # --- Capacity gauge (horizontal bar) ---
        gauge_x = 30
        gauge_y = y_top + 10
        gauge_w = 340
        gauge_h = 30
        fill_w = int(gauge_w * pct_used / 100)

        color = capacity_color(pct_used)
        self.create_rectangle(gauge_x, gauge_y, gauge_x + gauge_w, gauge_y + gauge_h,
                              outline='gray40', fill='gray15', tags='content')
        if fill_w > 0:
            self.create_rectangle(gauge_x, gauge_y, gauge_x + fill_w, gauge_y + gauge_h,
                                  outline='', fill=color, tags='content')

        # Sink name label (left of gauge)
        self.create_text(gauge_x, gauge_y - 3, text=sink_name.upper(),
                         fill=COLOR_TEXT, anchor='sw',
                         font=('TkDefaultFont', 12, 'bold'), tags='content')

        # Capacity text (right of gauge)
        cap_text = f"{format_bytes(used)} / {format_bytes(total)} ({pct_used:.0f}%)"
        self.create_text(gauge_x + gauge_w + 10, gauge_y + gauge_h // 2,
                         text=cap_text, fill=COLOR_TEXT, anchor='w',
                         font=('TkDefaultFont', 11), tags='content')

        # Free space prominently
        free_text = f"{format_bytes(free)} free"
        self.create_text(gauge_x + gauge_w + 10, gauge_y + gauge_h + 5,
                         text=free_text, fill=color, anchor='nw',
                         font=('TkDefaultFont', 10, 'bold'), tags='content')

        # --- Runway estimate ---
        runway_y = gauge_y + gauge_h + 30
        runway = self._compute_runway(disk)
        if runway is not None:
            if runway > 365:
                runway_text = f"Runway: > 1 year at current intake"
            else:
                runway_text = f"Runway: ~{runway} days at current intake"
            runway_color = capacity_color(pct_used)
        else:
            runway_text = "Runway: insufficient data for estimate"
            runway_color = COLOR_MUTED
        self.create_text(gauge_x, runway_y, text=runway_text,
                         fill=runway_color, anchor='nw',
                         font=('TkDefaultFont', 11), tags='content')

        # --- Coverage summary ---
        summary_y = runway_y + 25
        if daily is not None and len(daily) > 0:
            date_min = daily['date'].min()
            date_max = daily['date'].max()
            total_events = int(daily['event_count'].sum())
            total_images = int(daily['image_count'].sum())
            cov_text = f"{date_min} to {date_max}  •  {total_events:,} events  •  {total_images:,} images"
        else:
            cov_text = "No event data"
        self.create_text(gauge_x, summary_y, text=cov_text,
                         fill=COLOR_MUTED, anchor='nw',
                         font=('TkDefaultFont', 10), tags='content')

        # --- SentinelCam breakdown ---
        sc_y = summary_y + 22
        sc_bytes = latest.get('sentinelcam_bytes', 0)
        img_bytes = latest.get('image_bytes', 0)
        crop_bytes = latest.get('crop_bytes', 0)
        csv_bytes = latest.get('csv_bytes', 0)
        sc_text = (f"SentinelCam: {format_bytes(sc_bytes)} "
                   f"({format_bytes(img_bytes)} images, "
                   f"{format_bytes(crop_bytes)} crops, "
                   f"{format_bytes(csv_bytes)} CSV)")
        self.create_text(gauge_x, sc_y, text=sc_text,
                         fill=COLOR_MUTED, anchor='nw',
                         font=('TkDefaultFont', 10), tags='content')

        # --- Last scan timestamp ---
        ts_y = sc_y + 22
        if generated:
            if isinstance(generated, datetime):
                ts_text = f"Report generated: {generated.strftime('%Y-%m-%d %H:%M')} UTC"
            else:
                ts_text = f"Report generated: {generated}"
        else:
            ts_text = "Report generation time unknown"
        self.create_text(gauge_x, ts_y, text=ts_text,
                         fill='gray40', anchor='nw',
                         font=('TkDefaultFont', 9), tags='content')

        # --- Tap target for drill-down ---
        # Make the entire panel clickable to go to Level 2
        tap_id = self.create_rectangle(0, y_top, DISPLAY_W - 70, y_top + height,
                                       outline='', fill='', tags='content')
        self.tag_bind(tap_id, "<Button-1>",
                      lambda e, sn=sink_name: self._select_sink(sn))
        # Drill-down indicator
        self.create_text(DISPLAY_W - 80, y_top + height // 2,
                         text="▶", fill=COLOR_MUTED,
                         font=('TkDefaultFont', 18), tags='content')

    def _compute_runway(self, disk_summary):
        """Estimate days until disk is full based on recent free-space trend.

        Uses linear regression on the last 14+ data points of free_bytes
        to project when free space reaches zero.

        Returns
        -------
        int or None
            Estimated days remaining, or None if insufficient data
        """
        if disk_summary is None or len(disk_summary) < 3:
            return None

        # Use last 30 entries (days) for trend
        recent = disk_summary.tail(30).copy()
        if len(recent) < 3:
            return None

        # Convert scan_date to days-since-first for regression
        first_date = recent['scan_date'].iloc[0]
        if hasattr(first_date, 'timestamp'):
            days = np.array([(d.timestamp() - first_date.timestamp()) / 86400.0
                             for d in recent['scan_date']])
        else:
            days = np.arange(len(recent), dtype=float)

        free = recent['free_bytes'].values.astype(float)

        # Simple linear regression: free_bytes = m*days + b
        if days[-1] - days[0] < 1:
            return None  # not enough time span

        n = len(days)
        sum_x = days.sum()
        sum_y = free.sum()
        sum_xy = (days * free).sum()
        sum_x2 = (days * days).sum()
        denom = n * sum_x2 - sum_x * sum_x
        if abs(denom) < 1e-10:
            return None

        m = (n * sum_xy - sum_x * sum_y) / denom
        b = (sum_y - m * sum_x) / n

        if m >= 0:
            # Free space is not decreasing
            return 999  # effectively unlimited

        # Project when free = 0: 0 = m*t + current_free
        current_free = free[-1]
        days_remaining = -current_free / m

        return max(1, int(days_remaining))

    def _select_sink(self, sink_name):
        """Navigate to Level 2 for a specific data sink."""
        if sink_name not in self.reports:
            return
        self.selected_sink = sink_name
        self.level = 2
        self._show_daily_trend()

    # ------------------------------------------------------------------
    #  Level 2 — Daily Intake Trend
    # ------------------------------------------------------------------

    def _show_daily_trend(self, window_days=60):
        """Render Level 2: bar chart of daily image intake."""
        self.delete('content')
        self._draw_header()

        report = self.reports.get(self.selected_sink)
        if not report:
            return

        daily = report.get('daily_summary')
        if daily is None or len(daily) == 0:
            self.create_text(400, 240, text="No daily data available",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 14),
                             tags='content')
            return

        # Aggregate by date (all views combined)
        by_date = daily.groupby('date').agg(
            image_bytes=('image_bytes', 'sum'),
            image_count=('image_count', 'sum'),
            event_count=('event_count', 'sum'),
        ).reset_index()
        by_date = by_date.sort_values('date')

        # Filter to window
        if len(by_date) > window_days:
            by_date = by_date.tail(window_days)

        dates = by_date['date'].tolist()
        values_bytes = by_date['image_bytes'].tolist()
        values_gb = [b / (1024**3) for b in values_bytes]

        # Chart geometry
        chart_x = 80
        chart_y = 55
        chart_w = 650
        chart_h = 320
        chart_bottom = chart_y + chart_h

        max_val = max(values_gb) if values_gb else 1.0
        max_val = max(max_val, 0.1)  # avoid division by zero

        # Y-axis
        self.create_line(chart_x, chart_y, chart_x, chart_bottom,
                         fill='gray40', tags='content')
        # X-axis
        self.create_line(chart_x, chart_bottom, chart_x + chart_w, chart_bottom,
                         fill='gray40', tags='content')

        # Y-axis labels
        y_ticks = 5
        for i in range(y_ticks + 1):
            val = max_val * i / y_ticks
            y = chart_bottom - (chart_h * i / y_ticks)
            self.create_text(chart_x - 5, y, text=f"{val:.1f}",
                             fill=COLOR_MUTED, anchor='e',
                             font=('TkDefaultFont', 8), tags='content')
            if i > 0:
                self.create_line(chart_x, y, chart_x + chart_w, y,
                                 fill='gray20', tags='content')

        # Y-axis label
        self.create_text(15, chart_y + chart_h // 2, text="GB/day",
                         fill=COLOR_MUTED, anchor='w', angle=90,
                         font=('TkDefaultFont', 9), tags='content')

        # Bars
        n_bars = len(values_gb)
        if n_bars == 0:
            return
        bar_spacing = chart_w / n_bars
        bar_w = max(2, int(bar_spacing * 0.8))

        for i, (d, gb) in enumerate(zip(dates, values_gb)):
            bar_h = int(chart_h * gb / max_val) if max_val > 0 else 0
            x = chart_x + int(i * bar_spacing) + (int(bar_spacing) - bar_w) // 2
            y = chart_bottom - bar_h

            bar_id = self.create_rectangle(x, y, x + bar_w, chart_bottom,
                                           fill=COLOR_BAR, outline='',
                                           tags='content')
            # Tap bar for Level 3 drill-down
            self.tag_bind(bar_id, "<Button-1>",
                          lambda e, dt=d: self._select_date(dt))

        # X-axis date labels (show ~6 evenly spaced)
        label_count = min(6, n_bars)
        if label_count > 0:
            step = max(1, n_bars // label_count)
            for i in range(0, n_bars, step):
                x = chart_x + int(i * bar_spacing) + int(bar_spacing) // 2
                d = dates[i]
                # Show as MM-DD
                label = d[5:] if len(d) >= 10 else d
                self.create_text(x, chart_bottom + 12, text=label,
                                 fill=COLOR_MUTED, anchor='n',
                                 font=('TkDefaultFont', 8), tags='content')

        # Summary statistics below chart
        stats_y = chart_bottom + 35
        total_gb = sum(values_gb)
        avg_gb = total_gb / n_bars if n_bars > 0 else 0
        peak_gb = max(values_gb) if values_gb else 0
        peak_date = dates[values_gb.index(peak_gb)] if values_gb else "N/A"

        stats = (f"{n_bars} days shown  •  "
                 f"Avg: {avg_gb:.2f} GB/day  •  "
                 f"Peak: {peak_gb:.2f} GB on {peak_date}  •  "
                 f"Total: {total_gb:.1f} GB")
        self.create_text(400, stats_y, text=stats,
                         fill=COLOR_MUTED, anchor='n',
                         font=('TkDefaultFont', 9), tags='content')

        # Window selector buttons (30/60/90)
        btn_y = stats_y + 25
        for days, label in [(30, "30d"), (60, "60d"), (90, "90d")]:
            btn_x = 300 + (days - 30) * 3
            color = COLOR_TEXT if days == window_days else COLOR_MUTED
            btn_id = self.create_text(btn_x, btn_y, text=f"[{label}]",
                                      fill=color, anchor='n',
                                      font=('TkDefaultFont', 10, 'bold'),
                                      tags='content')
            self.tag_bind(btn_id, "<Button-1>",
                          lambda e, d=days: self._show_daily_trend(d))

    def _select_date(self, date_str):
        """Navigate to Level 3 for a specific date."""
        self.selected_dates = [date_str]
        self.level = 3
        self._show_per_view()

    # ------------------------------------------------------------------
    #  Level 3 — Per-View Breakdown
    # ------------------------------------------------------------------

    def _show_per_view(self):
        """Render Level 3: per-view storage breakdown for selected date(s)."""
        self.delete('content')
        self._draw_header()

        report = self.reports.get(self.selected_sink)
        if not report or not self.selected_dates:
            return

        daily = report.get('daily_summary')
        if daily is None or len(daily) == 0:
            return

        # Filter to selected dates
        mask = daily['date'].isin(self.selected_dates)
        filtered = daily[mask]
        if len(filtered) == 0:
            date_str = ', '.join(self.selected_dates)
            self.create_text(400, 240, text=f"No data for {date_str}",
                             fill=COLOR_MUTED, font=('TkDefaultFont', 14),
                             tags='content')
            return

        # Aggregate by view
        by_view = filtered.groupby(['node', 'viewname']).agg(
            image_bytes=('image_bytes', 'sum'),
            image_count=('image_count', 'sum'),
            event_count=('event_count', 'sum'),
            csv_bytes=('csv_bytes', 'sum'),
            crop_bytes=('crop_bytes', 'sum'),
            crop_count=('crop_count', 'sum'),
        ).reset_index()
        by_view = by_view.sort_values('image_bytes', ascending=False)

        # Date label
        date_label = ', '.join(self.selected_dates)
        self.create_text(400, 48, text=date_label,
                         fill=COLOR_MUTED, anchor='n',
                         font=('TkDefaultFont', 10), tags='content')

        # Horizontal bar chart
        chart_x = 180
        chart_y = 75
        chart_w = 500
        row_h = 55

        max_bytes = by_view['image_bytes'].max() if len(by_view) > 0 else 1
        max_bytes = max(max_bytes, 1)

        colors = [COLOR_BAR, COLOR_BAR_ALT, COLOR_GREEN, COLOR_YELLOW, COLOR_BLUE]

        for i, row in enumerate(by_view.itertuples(index=False)):
            y = chart_y + i * row_h
            if y + row_h > DISPLAY_H - 30:
                # Out of screen
                remaining = len(by_view) - i
                self.create_text(400, y + 10,
                                 text=f"... and {remaining} more views",
                                 fill=COLOR_MUTED, font=('TkDefaultFont', 10),
                                 tags='content')
                break

            # View label
            view_label = f"{row.viewname} ({row.node})"
            self.create_text(chart_x - 10, y + row_h // 2,
                             text=view_label, fill=COLOR_TEXT, anchor='e',
                             font=('TkDefaultFont', 10), tags='content')

            # Bar
            bar_w = int(chart_w * row.image_bytes / max_bytes)
            bar_w = max(bar_w, 2)
            color = colors[i % len(colors)]
            self.create_rectangle(chart_x, y + 5, chart_x + bar_w, y + row_h - 15,
                                  fill=color, outline='', tags='content')

            # Stats text on bar
            stats = f"{format_bytes(row.image_bytes)}  •  {row.image_count:,} imgs  •  {row.event_count:,} evts"
            text_x = chart_x + bar_w + 8
            if text_x + 200 > DISPLAY_W:
                text_x = chart_x + 8
            self.create_text(text_x, y + row_h // 2 - 3,
                             text=stats, fill=COLOR_TEXT, anchor='w',
                             font=('TkDefaultFont', 9), tags='content')

            # Average images per event, plus crop tally for crop-publishing views
            if row.event_count > 0:
                avg_imgs = row.image_count / row.event_count
                avg_text = f"~{avg_imgs:.0f} imgs/event"
                if getattr(row, 'crop_count', 0):
                    avg_text += f"  •  {row.crop_count:,} crops ({format_bytes(row.crop_bytes)})"
                self.create_text(chart_x, y + row_h - 12,
                                 text=avg_text, fill=COLOR_MUTED, anchor='nw',
                                 font=('TkDefaultFont', 8), tags='content')

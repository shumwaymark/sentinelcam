"""Calendar-based event history navigation for Sentinelcam watchtower

Replaces the flat EventListPage with a month-view calendar showing event
density per day, with touch-friendly drill-down to hourly time slots.
All calendar cells are pre-allocated canvas items updated via itemconfig().
The hourly drill-down uses a scrollable MenuPanel for touch-drag scrolling
on the Pi 7" touchscreen.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import calendar
import logging
import tkinter as tk
from tkinter import ttk
from datetime import date, datetime

import PIL.Image, PIL.ImageTk

logger = logging.getLogger("watchtower.calendar")

# Calendar grid geometry (800×480 display)
CAL_GRID_X = 105    # left edge of calendar grid
CAL_GRID_Y = 80     # top edge of first row
CAL_COLS = 7
CAL_ROWS = 6
CELL_W = 82         # cell width in pixels
CELL_H = 63         # cell height in pixels
COL_PITCH = 83      # cell width + 1px gap
ROW_PITCH = 64      # cell height + 1px gap

# Hourly drill-down (MenuPanel positioned in same area as calendar grid)
HOUR_PANEL_X = 105
HOUR_PANEL_Y = 45
HOUR_PANEL_W = 580
HOUR_PANEL_H = 430  # visible viewport height
HOUR_SLOT_H = 75    # height per hour slot item


class HourSlotItem(tk.Frame):
    """Lightweight hour slot widget for the drill-down view"""

    def __init__(self, parent, hour, event_count, first_idx, on_tap):
        tk.Frame.__init__(self, parent, bg='gray20', relief=tk.RAISED,
                          borderwidth=2, highlightthickness=0, width=550)
        self.first_idx = first_idx
        self.columnconfigure(0, weight=1)

        end_hour = (hour + 1) % 24
        time_str = f"{hour:02d}:00 – {end_hour:02d}:00"
        count_str = f"{event_count} event{'s' if event_count != 1 else ''}"

        self.time_label = tk.Label(self, text=time_str,
                                   font=('TkDefaultFont', 13, 'bold'),
                                   bg='gray20', fg='white', anchor='w',
                                   highlightthickness=0, borderwidth=0)
        self.time_label.grid(row=0, column=0, sticky='ew', padx=15, pady=(8, 2))

        self.count_label = tk.Label(self, text=count_str,
                                    font=('TkDefaultFont', 10),
                                    bg='gray20', fg='lightgray', anchor='w',
                                    highlightthickness=0, borderwidth=0)
        self.count_label.grid(row=1, column=0, sticky='ew', padx=15, pady=(0, 8))

        self.arrow_label = tk.Label(self, text="→",
                                    font=('TkDefaultFont', 18),
                                    bg='gray20', fg='chartreuse',
                                    highlightthickness=0, borderwidth=0)
        self.arrow_label.grid(row=0, column=1, rowspan=2, padx=15)

        for widget in [self.time_label, self.count_label, self.arrow_label]:
            widget.bind('<Button-1>', lambda e, idx=first_idx: on_tap(idx))


class CalendarPage(tk.Canvas):
    """Full-screen calendar event browser with month grid and hourly drill-down.

    Month view: pure canvas items (42 day cells), updated via itemconfig().
    Drill-down: MenuPanel with HourSlotItem widgets for touch-scrollable hour list.
    """

    def __init__(self, app, outpost_views):
        tk.Canvas.__init__(self, app.master, width=800, height=480,
                           borderwidth=0, highlightthickness=0, background="black")
        self.app = app
        self.outpost_views = outpost_views

        # State
        self.display_month = date.today().replace(day=1)
        self.selected_date = None
        self.drill_mode = False
        self.current_view = None
        self.last_event_count = 0
        self._day_index = {}
        self._hour_data = {}
        self._earliest_date = None
        self._latest_date = None

        self._build_canvas()

    # ------------------------------------------------------------------
    # Canvas construction (called once)
    # ------------------------------------------------------------------

    def _build_canvas(self):
        """Create all persistent canvas items. MenuPanel for drill-down is
        created lazily in _ensure_hour_panel() to avoid circular import
        during Application.__init__."""

        # --- Month title ---
        self._month_title = self.create_text(
            395, 20, anchor='center', text='',
            fill='white', font=('Arial', 16, 'bold'))

        # --- Day-of-week headers ---
        self._dow_labels = []
        for col, name in enumerate(['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']):
            x = CAL_GRID_X + col * COL_PITCH + CELL_W // 2
            t = self.create_text(x, 58, anchor='center', text=name,
                                 fill='#888888', font=('Arial', 10))
            self._dow_labels.append(t)

        # --- Calendar grid cells (42 items: 6 rows × 7 columns) ---
        self._cell_rect = []
        self._cell_day_text = []
        self._cell_count_text = []
        self._cell_data = [None] * 42  # (day_num, date_str) per cell

        for row in range(CAL_ROWS):
            for col in range(CAL_COLS):
                x0 = CAL_GRID_X + col * COL_PITCH
                y0 = CAL_GRID_Y + row * ROW_PITCH
                x1, y1 = x0 + CELL_W, y0 + CELL_H

                r = self.create_rectangle(x0, y0, x1, y1,
                                          fill='#0D0D0D', outline='#333333')
                dt = self.create_text(x0 + 6, y0 + 5, anchor='nw', text='',
                                      fill='gray', font=('Arial', 11))
                ct = self.create_text(x1 - 5, y1 - 5, anchor='se', text='',
                                      fill='gray', font=('Arial', 9))
                self._cell_rect.append(r)
                self._cell_day_text.append(dt)
                self._cell_count_text.append(ct)

                # Bind tap on all three items for this cell
                for item in (r, dt, ct):
                    self.tag_bind(item, '<ButtonPress-1>',
                                  lambda e, i=row * CAL_COLS + col: self._on_cell_tap(i))

        # --- Left panel: month navigation arrows ---
        self._prev_arrow = self.create_text(
            50, 180, text='◀', fill='#AAAAAA',
            font=('Arial', 20), anchor='center')
        self._next_arrow = self.create_text(
            50, 260, text='▶', fill='#AAAAAA',
            font=('Arial', 20), anchor='center')

        self._today_btn_rect = self.create_rectangle(
            10, 340, 90, 375, fill='#2A2A2A', outline='#555555')
        self._today_btn_text = self.create_text(
            50, 357, text='TODAY', fill='#AAAAAA',
            font=('Arial', 10), anchor='center')

        self.tag_bind(self._prev_arrow, '<ButtonPress-1>', lambda e: self._prev_month())
        self.tag_bind(self._next_arrow, '<ButtonPress-1>', lambda e: self._next_month())
        self.tag_bind(self._today_btn_rect, '<ButtonPress-1>', lambda e: self._goto_today())
        self.tag_bind(self._today_btn_text, '<ButtonPress-1>', lambda e: self._goto_today())

        # --- Left panel: back button (hidden until drill-down) ---
        self._back_text = self.create_text(
            50, 180, text='← Back', fill='#AAAAAA',
            font=('Arial', 12), anchor='center', state='hidden')
        self.tag_bind(self._back_text, '<ButtonPress-1>', lambda e: self._exit_drill_mode())

        # --- Right panel: close button and view label ---
        self.close_img = PIL.ImageTk.PhotoImage(file="images/close.png")
        close_id = self.create_image(730, 10, anchor="nw", image=self.close_img)
        self.tag_bind(close_id, "<Button-1>",
                      lambda e: self.app.show_page(self._player_page()))

        self._view_label = self.create_text(
            745, 240, anchor='center', text='', fill='#888888',
            font=('Arial', 9), angle=90)

        # --- Hourly drill-down: MenuPanel created lazily to avoid circular import ---
        self._hour_panel = None
        self._hour_panel_window = None

    # ------------------------------------------------------------------
    # Page enum helper
    # ------------------------------------------------------------------

    def _player_page(self):
        """Return the PLAYER page enum value."""
        from watchtower import UserPage
        return UserPage.PLAYER

    # ------------------------------------------------------------------
    # Data indexing
    # ------------------------------------------------------------------

    def _build_day_index(self, eventlist):
        """Build {date_str: {'count': int, 'first_idx': int}} from event list.
        Also updates _earliest_date and _latest_date for month nav bounds."""
        day_index = {}
        for idx, (timestamp, date_str, event_id, size) in enumerate(eventlist):
            if date_str not in day_index:
                day_index[date_str] = {'count': 0, 'first_idx': idx}
            day_index[date_str]['count'] += 1

        if day_index:
            sorted_dates = sorted(day_index.keys())
            self._earliest_date = sorted_dates[0]
            self._latest_date = sorted_dates[-1]
        else:
            self._earliest_date = None
            self._latest_date = None
        return day_index

    def _build_hour_index(self, eventlist, date_str):
        """Build {hour: {'count': int, 'first_idx': int}} for a given date."""
        hour_index = {}
        for idx, (timestamp, d, event_id, size) in enumerate(eventlist):
            if d != date_str:
                continue
            hour = timestamp.hour
            if hour not in hour_index:
                hour_index[hour] = {'count': 0, 'first_idx': idx}
            hour_index[hour]['count'] += 1
        return hour_index

    # ------------------------------------------------------------------
    # Month grid helpers
    # ------------------------------------------------------------------

    def _month_grid(self, year, month):
        """Return list of 42 (day_number_or_None, date_str_or_None) tuples.
        None entries are padding cells outside the month."""
        cal = calendar.Calendar(firstweekday=6)  # Sunday first
        weeks = cal.monthdayscalendar(year, month)
        grid = []
        for week in weeks:
            for day in week:
                if day == 0:
                    grid.append((None, None))
                else:
                    grid.append((day, f'{year}-{month:02d}-{day:02d}'))
        # Pad to exactly 42 cells (6 rows × 7 columns)
        while len(grid) < 42:
            grid.append((None, None))
        return grid[:42]

    # ------------------------------------------------------------------
    # Calendar rendering (month view)
    # ------------------------------------------------------------------

    def _draw_calendar(self):
        """Update all 42 day cells for the current display_month."""
        year = self.display_month.year
        month = self.display_month.month
        grid = self._month_grid(year, month)
        today_str = date.today().strftime('%Y-%m-%d')

        self.itemconfig(self._month_title,
                        text=f"{calendar.month_name[month]} {year}")

        for i, (day_num, date_str) in enumerate(grid):
            rect_id = self._cell_rect[i]
            day_id = self._cell_day_text[i]
            cnt_id = self._cell_count_text[i]
            self._cell_data[i] = (day_num, date_str)

            if day_num is None:
                # Padding cell — muted, not tappable
                self.itemconfig(rect_id, fill='#0D0D0D', outline='#1A1A1A', width=1)
                self.itemconfig(day_id, text='')
                self.itemconfig(cnt_id, text='')
            else:
                data = self._day_index.get(date_str)
                if data:
                    fill = '#4A3A00' if date_str == self.selected_date else '#1A4A1A'
                    self.itemconfig(rect_id, fill=fill, outline='#555555')
                    self.itemconfig(cnt_id, text=str(data['count']), fill='#AAAAAA')
                else:
                    self.itemconfig(rect_id, fill='#1A1A1A', outline='#333333')
                    self.itemconfig(cnt_id, text='')

                # Today highlight
                if date_str == today_str:
                    self.itemconfig(rect_id, outline='white', width=2)
                else:
                    self.itemconfig(rect_id, width=1)

                self.itemconfig(day_id, text=str(day_num), fill='#BBBBBB')

        # Update navigation arrow states
        self._update_nav_arrows()

    def _update_nav_arrows(self):
        """Dim arrows when at navigation boundaries."""
        year = self.display_month.year
        month = self.display_month.month
        display_ym = f"{year}-{month:02d}"
        current_ym = date.today().strftime('%Y-%m')

        earliest_ym = self._earliest_date[:7] if self._earliest_date else None
        prev_fill = '#444444' if (not earliest_ym or display_ym <= earliest_ym) else '#AAAAAA'
        next_fill = '#444444' if display_ym >= current_ym else '#AAAAAA'

        self.itemconfig(self._prev_arrow, fill=prev_fill)
        self.itemconfig(self._next_arrow, fill=next_fill)

    # ------------------------------------------------------------------
    # Month navigation
    # ------------------------------------------------------------------

    def _prev_month(self):
        """Navigate to previous month with data, skipping empty months."""
        earliest_ym = self._earliest_date[:7] if self._earliest_date else None
        display_ym = f"{self.display_month.year}-{self.display_month.month:02d}"
        if earliest_ym and display_ym <= earliest_ym:
            return  # already at earliest

        y, m = self.display_month.year, self.display_month.month
        # Walk backward through months, stop at first with data or earliest
        while True:
            m -= 1
            if m < 1:
                m = 12
                y -= 1
            candidate_ym = f"{y}-{m:02d}"
            if earliest_ym and candidate_ym < earliest_ym:
                # Went past earliest data — land on earliest month
                y = int(earliest_ym[:4])
                m = int(earliest_ym[5:7])
                break
            if self._month_has_data(y, m):
                break
        self.display_month = date(y, m, 1)
        self.selected_date = None
        self._draw_calendar()

    def _next_month(self):
        """Navigate to next month if not already at current month."""
        current_ym = date.today().strftime('%Y-%m')
        display_ym = f"{self.display_month.year}-{self.display_month.month:02d}"
        if display_ym >= current_ym:
            return  # already at current month

        y, m = self.display_month.year, self.display_month.month
        m += 1
        if m > 12:
            m = 1
            y += 1
        self.display_month = date(y, m, 1)
        self.selected_date = None
        self._draw_calendar()

    def _month_has_data(self, year, month):
        """Return True if any day in the given month has events."""
        prefix = f"{year}-{month:02d}"
        return any(d.startswith(prefix) for d in self._day_index)

    def _goto_today(self):
        """Jump to the current month."""
        self.display_month = date.today().replace(day=1)
        self.selected_date = None
        self._draw_calendar()

    # ------------------------------------------------------------------
    # Day cell tap
    # ------------------------------------------------------------------

    def _on_cell_tap(self, cell_idx):
        """Handle tap on a calendar day cell."""
        day_num, date_str = self._cell_data[cell_idx]
        if day_num is None:
            return  # padding cell
        if date_str not in self._day_index:
            return  # no events for this day

        self._enter_drill_mode(date_str)

    # ------------------------------------------------------------------
    # Hourly drill-down
    # ------------------------------------------------------------------

    def _ensure_hour_panel(self):
        """Lazily create the MenuPanel for hourly drill-down. Deferred from
        _build_canvas() to avoid circular import with watchtower module."""
        if self._hour_panel is not None:
            return
        from watchtower import MenuPanel
        content_height = HOUR_SLOT_H * 24
        self._hour_panel = MenuPanel(
            self, HOUR_PANEL_W, content_height,
            show_scrollbar=True, visible_height=HOUR_PANEL_H)
        self._hour_panel.interior.columnconfigure(0, weight=1)
        self._hour_panel_window = self.create_window(
            HOUR_PANEL_X, HOUR_PANEL_Y,
            window=self._hour_panel, anchor=tk.NW, state='hidden')

    def _enter_drill_mode(self, date_str):
        """Switch from calendar grid to hourly breakdown for the selected date."""
        self._ensure_hour_panel()

        view = self.outpost_views[self.app._current_view]
        # Rebuild hour index from the *current* eventlist to avoid stale data
        hour_data = self._build_hour_index(view.eventlist, date_str)
        if not hour_data:
            # Date had events when calendar was drawn but they are gone now
            logger.debug(f"No events found for {date_str} on drill-down (stale index)")
            self._day_index = self._build_day_index(view.eventlist)
            self.last_event_count = view.event_count()
            self._draw_calendar()
            return

        self.drill_mode = True
        self.selected_date = date_str
        self._hour_data = hour_data

        # Hide calendar grid elements
        for i in range(42):
            self.itemconfig(self._cell_rect[i], state='hidden')
            self.itemconfig(self._cell_day_text[i], state='hidden')
            self.itemconfig(self._cell_count_text[i], state='hidden')
        for lbl in self._dow_labels:
            self.itemconfig(lbl, state='hidden')

        # Update title to show selected date
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        self.itemconfig(self._month_title,
                        text=dt.strftime('%A, %B %d, %Y'))

        # Populate hour slots in the MenuPanel
        for child in self._hour_panel.interior.winfo_children():
            child.destroy()

        sorted_hours = sorted(self._hour_data.keys())
        for display_idx, hour in enumerate(sorted_hours):
            data = self._hour_data[hour]
            item = HourSlotItem(
                self._hour_panel.interior,
                hour, data['count'], data['first_idx'],
                self._on_hour_tap)
            item.grid(row=display_idx, column=0, sticky='w', padx=10, pady=6)

        # Update scroll region
        self._hour_panel.interior.update_idletasks()
        bbox = self._hour_panel.canvas.bbox("all")
        if bbox:
            self._hour_panel.canvas.config(scrollregion=bbox)
        self._hour_panel.canvas.yview_moveto(0)
        self._hour_panel.scrollposition = 1

        # Show the hour panel
        self.itemconfig(self._hour_panel_window, state='normal')

        # Swap left panel: hide month arrows, show back button
        self.itemconfig(self._prev_arrow, state='hidden')
        self.itemconfig(self._next_arrow, state='hidden')
        self.itemconfig(self._today_btn_rect, state='hidden')
        self.itemconfig(self._today_btn_text, state='hidden')
        self.itemconfig(self._back_text, state='normal')

    def _exit_drill_mode(self):
        """Return from hourly breakdown to calendar grid."""
        self.drill_mode = False

        # Hide hour panel (if it was created)
        if self._hour_panel_window is not None:
            self.itemconfig(self._hour_panel_window, state='hidden')
        if self._hour_panel is not None:
            for child in self._hour_panel.interior.winfo_children():
                child.destroy()

        # Restore calendar grid and headers
        for i in range(42):
            self.itemconfig(self._cell_rect[i], state='normal')
            self.itemconfig(self._cell_day_text[i], state='normal')
            self.itemconfig(self._cell_count_text[i], state='normal')
        for lbl in self._dow_labels:
            self.itemconfig(lbl, state='normal')

        # Restore left panel navigation
        self.itemconfig(self._prev_arrow, state='normal')
        self.itemconfig(self._next_arrow, state='normal')
        self.itemconfig(self._today_btn_rect, state='normal')
        self.itemconfig(self._today_btn_text, state='normal')
        self.itemconfig(self._back_text, state='hidden')

        # Redraw calendar (selected_date preserved for amber highlight)
        self._draw_calendar()

    def _on_hour_tap(self, first_idx):
        """Handle tap on an hourly slot — navigate to event in player."""
        logger.debug(f"Hour slot selected, jumping to event {first_idx}")
        self.app.eventIdx = first_idx
        self.app.select_event(first_idx)
        self.app.show_page(self._player_page())

    # ------------------------------------------------------------------
    # Public API (called from Application.show_page)
    # ------------------------------------------------------------------

    def refresh_calendar(self):
        """Rebuild calendar data and redraw. Called on page entry."""
        if not self.app._current_view:
            return

        view = self.outpost_views[self.app._current_view]
        count = view.event_count()

        # Skip rebuild if nothing changed (same view, same count, not in drill mode)
        if (not self.drill_mode
                and self.current_view == self.app._current_view
                and self.last_event_count == count):
            return

        self.current_view = self.app._current_view
        self.last_event_count = count

        # Update view label
        self.itemconfig(self._view_label, text=view.description)

        # If view changed, reset to current month and exit drill mode
        if self.drill_mode:
            self._exit_drill_mode()
            return  # _exit_drill_mode calls _draw_calendar via refresh loop

        self._day_index = self._build_day_index(view.eventlist)
        self._draw_calendar()

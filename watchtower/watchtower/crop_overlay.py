"""Selected-crop overlay for the Sentinelcam Watchtower kiosk (§7.1).

The crop pipeline (Phases 5/6) makes a high-resolution, class-specific crop
available for the subjects in an event. This module picks the single most
representative crop for an event and (slice 2) composites it over the scene as
a glanceable "this just happened" readout — raised on replay-end, on pause, and
on a new-event push.

Slice 1 (this file): selection only — render-agnostic, unit-testable without a
display. `CropSelector.select()` returns a `SelectedCrop` (jpeg bytes + scene
bbox + label) or None when an event has no usable crop.

Selection is a pluggable slot, vehicle-first today:
  - Vehicle: the fastest subject (ranked from the `vsp` VASCAR data) — its crop
    + mph label. Available now; vsp.objid == trk.objid == crp.objid (§7.8).
  - Otherwise: the most-cropped subject, labelled by its class ("person", etc.).
    The person→recognized-name fill drops in once face-rec is crop-fed (§6.3).

Join chain (single source of truth, §4.5):
  crop JPEG ──(event, objid, seqnum)──▶ crp record ──(objid, ts)──▶ trk bbox
The crp record names the crop file; the scene bbox comes from the trk record for
the same objid nearest the crop's timestamp (the crp/trk planes share objid and
capture timestamps), never crop → trk directly.

Copyright (c) 2026 by Mark K Shumway, mark.shumway@swanriver.dev
License: MIT, see the sentinelcam LICENSE for more details.
"""

import logging

import numpy as np

logger = logging.getLogger("watchtower.crop_overlay")

# Phase preference when more than one crop exists for the chosen subject — the
# centre phase frames the subject best; entry next; far last (subject often
# facing away). Tolerate the US/UK spelling either way.
PHASE_PREFERENCE = ("centre", "center", "entry", "far")

# Smallest crop worth showing. Real crops are hundreds of pixels on a side
# (256x384 person, 512x192 vehicle); anything at this scale is the datapump's
# 1x1 missing-file placeholder, not a subject. See _usable_crop.
MIN_CROP_PX = 32


class SelectedCrop:
    """One chosen crop for an event, ready for the overlay render (slice 2)."""

    def __init__(self, jpg, bbox, label, objid, classname, phase):
        self.jpg = jpg              # crop JPEG bytes
        self.bbox = bbox            # (x1, y1, x2, y2) in scene coords, or None
        self.label = label          # overlay text, e.g. "42.0 mph" or "person"
        self.objid = objid          # persistent HostTracker tid
        self.classname = classname  # crop class, e.g. "vehicle" / "person"
        self.phase = phase          # traversal phase the crop came from

    def __repr__(self):
        n = len(self.jpg) if self.jpg else 0
        return (f"SelectedCrop(objid={self.objid}, class={self.classname}, "
                f"phase={self.phase}, label={self.label!r}, bbox={self.bbox}, "
                f"jpg={n}B)")


class CropSelector:
    """Pick the most representative crop for an event, for the kiosk overlay."""

    def select(self, feed, date, event):
        """Return a SelectedCrop for the event, or None if no usable crop.

        `feed` is a DataFeed already connected to the event's datapump.
        Never raises — any data shortfall yields None so the caller (player
        thread) simply skips the overlay rather than disrupting playback.
        """
        try:
            crp = self._tracking(feed, date, event, "crp")
            if crp is None or len(crp) == 0:
                return None  # no crops captured for this event

            objid, label = self._pick_by_speed(feed, date, event)
            if objid is None:
                objid, label = self._pick_most_cropped(crp), None

            crow = self._best_crop_row(crp, objid)
            if crow is None:
                return None

            objid = int(crow["objid"])
            seqnum = int(crow["seqnum"])
            classname = str(crow["classname"])
            phase = str(crow["phase"])
            if label is None:
                label = classname

            jpg = feed.get_crop_jpg(date, event, objid, seqnum, classname, phase)
            if not self._usable_crop(jpg):
                return None

            bbox = self._bbox_for(feed, date, event, objid, crow["timestamp"])
            return SelectedCrop(jpg, bbox, label, objid, classname, phase)
        except Exception as e:
            logger.warning(f"Crop selection failed for {date}/{event}: {e}")
            return None

    # ------------------------------------------------------------------
    #  Selection strategies (the pluggable slot)
    # ------------------------------------------------------------------

    def _pick_by_speed(self, feed, date, event):
        """Vehicle-first: the fastest subject from the vsp VASCAR data.

        Returns (objid, label) or (None, None) when there is no speed data.
        The label is the stored vsp classname (already "{mph} mph", §7.8).
        """
        vsp = self._tracking(feed, date, event, "vsp")
        if vsp is None or len(vsp) == 0 or "classname" not in vsp.columns:
            return None, None
        mph = vsp["classname"].map(self._parse_mph)
        ranked = vsp.assign(_mph=mph).dropna(subset=["_mph"])
        if len(ranked) == 0:
            return None, None
        top = ranked.loc[ranked["_mph"].idxmax()]
        return int(top["objid"]), str(top["classname"])

    def _pick_most_cropped(self, crp):
        """Fallback: the subject with the most crops (the best-observed one)."""
        return crp["objid"].value_counts().idxmax()

    # ------------------------------------------------------------------
    #  Crop + geometry resolution
    # ------------------------------------------------------------------

    def _best_crop_row(self, crp, objid):
        """Best crop record for a subject — preferred phase, else any."""
        sub = crp[crp["objid"] == objid]
        if len(sub) == 0:
            sub = crp  # subject had speed but no crop; fall back to the field
            if len(sub) == 0:
                return None
        rank = sub["phase"].map(
            lambda p: PHASE_PREFERENCE.index(p) if p in PHASE_PREFERENCE
            else len(PHASE_PREFERENCE))
        return sub.loc[rank.idxmin()]

    def _bbox_for(self, feed, date, event, objid, timestamp):
        """Scene bbox = the trk record for this objid nearest the crop's time."""
        trk = self._tracking(feed, date, event, "trk")
        if trk is None or len(trk) == 0:
            return None
        sub = trk[trk["objid"] == objid]
        if len(sub) == 0:
            return None
        nearest = (sub["timestamp"] - timestamp).abs().idxmin()
        row = sub.loc[nearest]
        try:
            return (int(row["rect_x1"]), int(row["rect_y1"]),
                    int(row["rect_x2"]), int(row["rect_y2"]))
        except (KeyError, ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _usable_crop(jpg):
        """False for the datapump's missing-file placeholder.

        A crp record whose JPEG never landed is a normal outcome — the record
        and the image travel over separate sockets and fail separately (§4.4).
        The datapump answers a missing crop with a 1x1 black pixel rather than
        an error, and the render path would faithfully enlarge that into a black
        card filling the kiosk. Reject it here so the caller falls back to the
        scene: a missing crop should read as absent, not as broken."""
        if not jpg:
            return False
        try:
            import simplejpeg
            h, w = simplejpeg.decode_jpeg_header(jpg)[:2]
            return w >= MIN_CROP_PX and h >= MIN_CROP_PX
        except Exception:
            return True  # unreadable header — let the render path have its say

    @staticmethod
    def _parse_mph(classname):
        """Parse mph from a vsp classname like '42.0 mph'; None if not a speed."""
        try:
            s = str(classname)
            if "mph" not in s:
                return None
            return float(s.split()[0])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _tracking(feed, date, event, trktype):
        """get_tracking_data wrapper that returns None on empty/missing sets."""
        try:
            return feed.get_tracking_data(date, event, trktype)
        except Exception:
            return None


def render_centered_card(scene, selected, dim=0.35, max_w_frac=0.72, max_h_frac=0.62):
    """Composite the selected crop as a centered card over a dimmed scene (§7.1).

    The chosen kiosk presentation: the scene dims to a backdrop and the high-res
    crop is shown enlarged and centered with a label caption — the crop is the
    "this just happened" payoff, the scene is context. The subject's bbox is
    drawn subtly on the backdrop for a "where in the scene" cue.

    Returns a NEW BGR image — the scene is copied, never modified in place (the
    caller's frame may be a view into a shared ring buffer). On any decode
    failure the original scene is returned unchanged so playback is undisturbed.
    """
    import cv2
    import simplejpeg
    try:
        crop = simplejpeg.decode_jpeg(selected.jpg, colorspace='BGR')
    except Exception as e:
        logger.warning(f"Crop decode failed for overlay: {e}")
        return scene

    H, W = scene.shape[:2]
    out = scene.copy()

    # Dim the scene to a backdrop (out *= dim)
    cv2.addWeighted(out, dim, np.zeros_like(out), 1.0 - dim, 0, out)

    # Subtle subject bbox on the backdrop, for "where in the scene" context
    if selected.bbox is not None:
        x1, y1, x2, y2 = selected.bbox
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 220, 0), 1, cv2.LINE_AA)

    # Scale the crop to fit a centered box, preserving aspect
    ch, cw = crop.shape[:2]
    scale = min((W * max_w_frac) / cw, (H * max_h_frac) / ch)
    sw, sh = max(1, int(cw * scale)), max(1, int(ch * scale))
    crop = cv2.resize(crop, (sw, sh), interpolation=cv2.INTER_AREA)
    x0, y0 = (W - sw) // 2, (H - sh) // 2
    out[y0:y0 + sh, x0:x0 + sw] = crop

    # White card border
    cv2.rectangle(out, (x0 - 2, y0 - 2), (x0 + sw + 1, y0 + sh + 1),
                  (255, 255, 255), 2)

    # Caption banner along the bottom of the card
    label = selected.label or selected.classname
    if label:
        font, fscale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2
        (tw, th), base = cv2.getTextSize(label, font, fscale, thick)
        bh = th + base + 16
        by1 = max(y0, y0 + sh - bh)
        band = out.copy()
        cv2.rectangle(band, (x0, by1), (x0 + sw, y0 + sh), (0, 0, 0), -1)
        cv2.addWeighted(band, 0.55, out, 0.45, 0, out)
        tx = x0 + max(0, (sw - tw) // 2)
        ty = y0 + sh - (bh - th) // 2
        cv2.putText(out, label, (tx, ty), font, fscale,
                    (255, 255, 255), thick, cv2.LINE_AA)

    return out

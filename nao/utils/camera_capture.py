# -*- coding: utf-8 -*-

# utils/camera_capture.py (Python 2.7)

from naoqi import ALProxy
import os

def _to_str_path(p):
    # Normalize NAO return types
    if isinstance(p, (list, tuple)) and p:
        p = p[0]
    try:
        basestring
    except NameError:
        basestring = (str, bytes)
    if not isinstance(p, basestring):
        p = str(p)
    return p

def capture_photo(nao_ip="127.0.0.1", port=9559, out_path="/home/nao/face.jpg"):
    cam = ALProxy("ALPhotoCapture", nao_ip, port)
    try:
        cam.setCameraID(0)  # top cam
    except Exception:
        pass
    cam.setResolution(2)         # 640x480
    cam.setPictureFormat("jpg")

    directory = os.path.dirname(out_path) or "/home/nao"
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
        except:
            pass

    base = os.path.splitext(os.path.basename(out_path))[0]

    
    saved_path = None
    try:
        ret = cam.takePictures(1, directory, base)   # often returns ["/home/nao/base_0.jpg"]
        saved_path = _to_str_path(ret)
    except Exception:
        try:
            # Older API: takePicture(folder, file) -> "/home/nao/file.jpg" or [path]
            ret = cam.takePicture(directory, base)
            saved_path = _to_str_path(ret)
        except Exception:
            return None

    # Normalize final path to requested out_path
    if not saved_path:
        return None

    if saved_path != out_path:
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
            if os.path.exists(saved_path):
                os.rename(saved_path, out_path)
                saved_path = out_path
            else:
                # sometimes NAO returns relative; try join
                cand = os.path.join(directory, os.path.basename(saved_path))
                if os.path.exists(cand):
                    os.rename(cand, out_path)
                    saved_path = out_path
        except Exception:
            # if rename fails but original exists, return original path
            pass

    return saved_path


def snap_quick(nao_ip, port=9559, resolution=1, color_space=11, path=None, leds=None):
    """Capture a quick 640x480 JPEG via ALPhotoCapture. Returns local path or None on failure.

    resolution=1 -> kQVGA (640x480); color_space=11 -> kRGBColorSpace.

    Privacy cue (Phase 6, ``green-led-cue``)
    ---------------------------------------
    When the optional ``leds`` argument is a ``LedDriver`` (see
    ``nao/leds.py``), this function flashes the right ear LED group **green**
    while the camera is active. The flash signals "camera-active" to anyone
    near the robot -- a visible, hard-to-miss privacy cue per the Phase 6
    PRD. Sequence:

      1. Fade the right-ear group to ``COLOR_GREEN`` over ~50 ms (so the LED
         is unmistakably green by the time ``takePicture`` fires).
      2. Run the existing capture.
      3. In a ``finally`` block, fade the right-ear group back to off
         (``(0.0, 0.0, 0.0)``) over ~100 ms. Total green-on time is well
         under the 200 ms budget called out in the Phase 6 task map.

    If ``leds`` is ``None`` (the default), behaviour is unchanged from prior
    revisions -- we just capture without any LED activity. This preserves
    backwards compatibility with every existing caller.

    The driver is consulted defensively:
      * ``leds._disabled`` (set by ``LedDriver`` when running off-robot or
        when ``ALProxy`` failed to construct) skips both fades, so the
        function works fine in unit/dev environments.
      * ``EAR_RIGHT_GROUP`` is read via ``getattr`` with the documented
        ALLeds group name ``"RightEarLeds"`` as a fallback. That keeps the
        fade working even on builds of ``leds.py`` that haven't yet added
        the constant. ``COLOR_GREEN`` falls back to a sensible default
        ``(0.1, 0.9, 0.3)`` for the same reason.
      * Each fade call is wrapped in ``try/except Exception``: if the LED
        ring momentarily refuses a command we don't want the camera capture
        to fail. The capture is the load-bearing operation; the LED cue is
        cosmetic.
    """
    # Fire the "camera-active" green cue *before* the capture so it's lit by
    # the time ALPhotoCapture latches the frame. Defensive getattr keeps
    # this working against older revisions of ``nao/leds.py``.
    if leds is not None and not getattr(leds, "_disabled", False):
        try:
            leds.fade(
                getattr(leds, "EAR_RIGHT_GROUP", "RightEarLeds"),
                getattr(leds, "COLOR_GREEN", (0.1, 0.9, 0.3)),
                0.05,
            )
        except Exception:
            # LED hiccups must not block the capture path. Swallowed here
            # rather than let the outer try/except return None.
            pass

    try:
        try:
            from naoqi import ALProxy
            import time, os
            photo = ALProxy("ALPhotoCapture", nao_ip, port)
            photo.setResolution(resolution)
            photo.setPictureFormat("jpg")
            out_dir = "/home/nao/snaps"
            try: os.makedirs(out_dir)
            except OSError: pass
            name = "snap_{0}".format(int(time.time() * 1000))
            photo.takePicture(out_dir, name)
            full = os.path.join(out_dir, name + ".jpg")
            return full if os.path.exists(full) else None
        except Exception:
            return None
    finally:
        # Always extinguish the cue, even on capture failure -- otherwise a
        # broken capture would leave the green LED stuck on, miscommunicating
        # an active camera to anyone watching.
        if leds is not None and not getattr(leds, "_disabled", False):
            try:
                leds.fade(
                    getattr(leds, "EAR_RIGHT_GROUP", "RightEarLeds"),
                    (0.0, 0.0, 0.0),
                    0.10,
                )
            except Exception:
                pass

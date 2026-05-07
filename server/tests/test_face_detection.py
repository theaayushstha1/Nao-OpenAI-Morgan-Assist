"""Phase 3 unit tests — face geometry helpers.

Exercises the new helpers added to ``nao/utils/face_naoqi.py`` per
``docs/PHASE_3_TASK_MAP.md`` § "nao/utils/face_naoqi.py — extensions":

    detect_faces_with_geometry(face_detection, memory, max_age_ms=200)
    closest_face(faces)
    is_mutually_gazing(face, yaw_tolerance_deg=15, pitch_tolerance_deg=15)

The helpers are owned by the sibling ``face-detection-extend`` agent
and may not have landed yet. ``pytest.importorskip`` plus targeted
``hasattr`` skips keep the file collectable in any worktree.

The distance estimator behind ``detect_faces_with_geometry`` is the
"face-size-in-image-frame" heuristic that the task map calls out: a
larger fraction of the frame width = closer face. The unit test for
that uses a synthetic ALMemory blob shaped exactly like ALFaceDetection
emits on the robot — see ``_fake_facedetected_blob`` for the layout.

No naoqi import here; the helpers are pure Python on top of the data
ALMemory hands them. We only need to feed them the right shape.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# ALMemory "FaceDetected" blob shape — derived from the NAOqi v2.8 docs.
#
# The published shape is:
#   FaceDetected = [TimeStamp, [FaceInfo[N], TimeFilteredReco], CameraPose,
#                   CurrentCameraName, ScanResult]
# where each FaceInfo = [ ShapeInfo, ExtraInfo ] and
#       ShapeInfo  = [ alpha, beta, sizeX, sizeY ]   (radians, image-frame)
#       ExtraInfo  = [ faceID, scoreReco, faceLabel, ... ]
#
# alpha/beta are the face center yaw/pitch in radians; sizeX/sizeY are the
# face bounding box width/height as fractions of the camera frame width.
# ─────────────────────────────────────────────────────────────────────────────


def _shape_info(alpha_rad: float, beta_rad: float,
                size_x: float, size_y: float | None = None) -> list[float]:
    """Build a FaceInfo[0] (ShapeInfo) list."""
    return [float(alpha_rad), float(beta_rad), float(size_x),
            float(size_y if size_y is not None else size_x)]


def _extra_info(face_id: float | int, score: float = 0.0,
                label: str = "") -> list[Any]:
    """Build a FaceInfo[1] (ExtraInfo) list. NAOqi packs extra arrays
    after the label; we keep them empty for the unit-test path."""
    return [int(face_id), float(score), str(label), [], []]


def _face_info(alpha_rad: float, beta_rad: float, size_x: float,
               face_id: int = 0, score: float = 0.0,
               label: str = "") -> list[Any]:
    """Build one FaceInfo entry."""
    return [_shape_info(alpha_rad, beta_rad, size_x),
            _extra_info(face_id, score, label)]


def _fake_facedetected_blob(faces: list[list[Any]],
                             timestamp_s: int = 1000,
                             timestamp_us: int = 0) -> list[Any]:
    """Wrap a list of FaceInfo entries into the full ``FaceDetected`` shape."""
    return [
        [timestamp_s, timestamp_us],
        # Inner list is [FaceInfo*, TimeFilteredReco]; the trailing
        # element is empty for our tests.
        list(faces) + [[]],
        # CameraPose, CurrentCameraName, ScanResult — placeholders.
        [0.0] * 6,
        "CameraTop",
        0,
    ]


def _fake_memory(face_blob: list[Any] | None) -> Any:
    """Return a minimal ALMemory stand-in whose ``getData("FaceDetected")``
    returns the supplied blob (or ``[]`` for "no face")."""

    class _Memory:
        def getData(self, key: str) -> Any:
            if key == "FaceDetected":
                return face_blob if face_blob is not None else []
            return None

    return _Memory()


def _fake_face_detection() -> Any:
    """Return a no-op face_detection proxy. Most helpers don't need
    anything beyond ``isSubscribed`` / ``subscribe`` shapes; we hand
    them a SimpleNamespace and let the helper ignore unknown methods."""
    return types.SimpleNamespace(
        subscribe=lambda *_a, **_k: None,
        unsubscribe=lambda *_a, **_k: None,
        isSubscribed=lambda *_a, **_k: True,
    )


def _stub_naoqi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub naoqi/qi modules so ``nao.utils.face_naoqi`` imports clean
    on dev machines where the real SDK is missing."""
    monkeypatch.setitem(sys.modules, "qi", types.SimpleNamespace(Session=lambda: None))
    monkeypatch.setitem(sys.modules, "naoqi",
                        types.SimpleNamespace(ALProxy=lambda *_a, **_k: None))


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_distance_estimate_from_face_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """A face occupying ~30% of the horizontal frame width should map to
    ~0.5 m, within ±20% tolerance.

    The task map calls out "approximate distance using the
    ALFaceDetection 'face size in image' heuristic and the known camera
    FOV". For the standard NAO H25 top camera (60.97° HFOV at VGA) and
    a typical adult face width of ~16 cm, simple pinhole geometry gives:

        d ≈ (face_width_m) / (2 * size_x * tan(HFOV / 2))
        d ≈ 0.16 / (2 * 0.30 * tan(30.5°))
        d ≈ 0.16 / (2 * 0.30 * 0.589)
        d ≈ 0.45 m

    We accept anywhere in ``[0.40, 0.60]`` m to leave room for any
    reasonable camera-FOV / face-width constant the implementation
    picks; that's the ±20% band the task map calls out.
    """
    _stub_naoqi(monkeypatch)
    pytest.importorskip("nao.utils.face_naoqi")
    from nao.utils import face_naoqi  # type: ignore[import-not-found]

    if not hasattr(face_naoqi, "detect_faces_with_geometry"):
        pytest.skip("detect_faces_with_geometry not implemented yet")

    blob = _fake_facedetected_blob([
        # alpha/beta = 0 (centered), size_x = 0.30 (30% of frame width).
        _face_info(0.0, 0.0, 0.30, face_id=1, score=0.85, label="alice"),
    ])
    memory = _fake_memory(blob)
    face_detection = _fake_face_detection()

    faces = face_naoqi.detect_faces_with_geometry(face_detection, memory)
    assert isinstance(faces, list), "detect_faces_with_geometry must return a list"
    assert len(faces) == 1, "expected one face"
    f = faces[0]

    distance = float(f.get("distance_m"))
    assert 0.40 <= distance <= 0.60, (
        "30%% face-width should map to ~0.5 m; got %r" % distance
    )

    # While we're here, sanity-check the rest of the geometry block:
    # implementations may report the face_id as int or string, and may
    # convert alpha/beta from radians to degrees. We don't assert on
    # exact units beyond "yaw and pitch present and finite".
    assert "yaw_deg" in f or "yaw_rad" in f, "yaw must be present in some form"
    assert "pitch_deg" in f or "pitch_rad" in f, "pitch must be present in some form"
    assert "confidence" in f
    assert 0.0 <= float(f.get("confidence", 0.0)) <= 1.0


def test_closest_face_returns_smallest_distance(monkeypatch: pytest.MonkeyPatch) -> None:
    """``closest_face`` returns the entry with the smallest ``distance_m``."""
    _stub_naoqi(monkeypatch)
    pytest.importorskip("nao.utils.face_naoqi")
    from nao.utils import face_naoqi  # type: ignore[import-not-found]

    if not hasattr(face_naoqi, "closest_face"):
        pytest.skip("closest_face not implemented yet")

    faces = [
        {"face_id": "a", "confidence": 0.7, "distance_m": 1.8,
         "yaw_deg": 0.0, "pitch_deg": 0.0, "name": None},
        {"face_id": "b", "confidence": 0.5, "distance_m": 0.6,
         "yaw_deg": 5.0, "pitch_deg": 2.0, "name": None},
        {"face_id": "c", "confidence": 0.6, "distance_m": 1.2,
         "yaw_deg": 10.0, "pitch_deg": -5.0, "name": None},
    ]
    chosen = face_naoqi.closest_face(faces)
    assert chosen is not None, "closest_face must not return None for non-empty list"
    assert chosen["face_id"] == "b", (
        "closest face must be 'b' at 0.6 m; got %r" % chosen.get("face_id")
    )

    # Empty list contract: per the task map this returns ``None``.
    empty = face_naoqi.closest_face([])
    assert empty is None, (
        "closest_face([]) must return None; got %r" % empty
    )


def test_closest_face_breaks_ties_by_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """When two faces share the same distance, the higher-confidence one wins."""
    _stub_naoqi(monkeypatch)
    pytest.importorskip("nao.utils.face_naoqi")
    from nao.utils import face_naoqi  # type: ignore[import-not-found]

    if not hasattr(face_naoqi, "closest_face"):
        pytest.skip("closest_face not implemented yet")

    faces = [
        {"face_id": "low_conf", "confidence": 0.4, "distance_m": 1.0,
         "yaw_deg": 5.0, "pitch_deg": 0.0},
        {"face_id": "high_conf", "confidence": 0.85, "distance_m": 1.0,
         "yaw_deg": 0.0, "pitch_deg": 0.0},
        {"face_id": "far", "confidence": 0.95, "distance_m": 2.5,
         "yaw_deg": 30.0, "pitch_deg": 5.0},
    ]
    chosen = face_naoqi.closest_face(faces)
    assert chosen is not None
    assert chosen["face_id"] == "high_conf", (
        "with tied distance, the higher-confidence face must win; got %r"
        % chosen.get("face_id")
    )


def test_is_mutually_gazing_within_tolerance(monkeypatch: pytest.MonkeyPatch) -> None:
    """A roughly head-on face is mutually gazing.

    "Roughly head-on" means yaw and pitch within the documented ±15°
    tolerance window. We test a face at (yaw=8°, pitch=5°) which is
    well inside the window from any direction.
    """
    _stub_naoqi(monkeypatch)
    pytest.importorskip("nao.utils.face_naoqi")
    from nao.utils import face_naoqi  # type: ignore[import-not-found]

    if not hasattr(face_naoqi, "is_mutually_gazing"):
        pytest.skip("is_mutually_gazing not implemented yet")

    head_on = {"face_id": "x", "confidence": 0.6, "distance_m": 1.0,
               "yaw_deg": 8.0, "pitch_deg": 5.0}
    assert face_naoqi.is_mutually_gazing(head_on) is True, (
        "yaw=8°, pitch=5° must register as mutual gaze (default ±15° tolerance)"
    )

    # And exactly at the boundary should still pass (inclusive).
    boundary = {"face_id": "y", "confidence": 0.6, "distance_m": 1.0,
                "yaw_deg": 15.0, "pitch_deg": 15.0}
    assert face_naoqi.is_mutually_gazing(boundary) is True, (
        "yaw=15°, pitch=15° (default tolerance boundary) should be inclusive"
    )

    # Custom-tighter tolerance — implementations should honor the kwargs.
    tight = {"face_id": "z", "confidence": 0.6, "distance_m": 1.0,
             "yaw_deg": 8.0, "pitch_deg": 5.0}
    # 5° tolerance: yaw of 8° exceeds it.
    assert face_naoqi.is_mutually_gazing(
        tight, yaw_tolerance_deg=5, pitch_tolerance_deg=5
    ) is False, "yaw=8° must fail a ±5° tolerance window"


def test_is_mutually_gazing_rejects_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """A profile face (head turned ~60° to one side) is NOT mutually gazing.

    Real-world: someone walking past the robot with their head turned
    away. The classic false-wake case the PRD calls out under
    "passerby" ('zero AWARE→ENGAGED transitions')."""
    _stub_naoqi(monkeypatch)
    pytest.importorskip("nao.utils.face_naoqi")
    from nao.utils import face_naoqi  # type: ignore[import-not-found]

    if not hasattr(face_naoqi, "is_mutually_gazing"):
        pytest.skip("is_mutually_gazing not implemented yet")

    profile = {"face_id": "p", "confidence": 0.55, "distance_m": 1.2,
               "yaw_deg": 60.0, "pitch_deg": 5.0}
    assert face_naoqi.is_mutually_gazing(profile) is False, (
        "yaw=60° must not register as mutual gaze (well outside ±15°)"
    )

    # Pitch too — looking down at a phone while standing in front of NAO.
    looking_down = {"face_id": "q", "confidence": 0.6, "distance_m": 0.9,
                    "yaw_deg": 0.0, "pitch_deg": -45.0}
    assert face_naoqi.is_mutually_gazing(looking_down) is False, (
        "pitch=-45° must not register as mutual gaze"
    )

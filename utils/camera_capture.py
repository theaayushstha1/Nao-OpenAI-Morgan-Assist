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

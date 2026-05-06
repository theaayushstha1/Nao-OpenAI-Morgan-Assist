# -*- coding: utf-8 -*-
"""Clear NAO-side face identity state.

Run on the robot when you want a clean face relearn:
    python /home/nao/nao_assist/reset_identity.py
"""
from __future__ import print_function

import config
from naoqi import ALProxy
from utils import user_cache


def main():
    user_cache.clear()
    face = ALProxy("ALFaceDetection", config.NAO_IP, config.NAO_PORT)
    try:
        before = face.getLearnedFacesList()
    except Exception:
        before = []
    try:
        face.clearDatabase()
    except Exception as e:
        print("[reset_identity] clearDatabase failed:", e)
        raise
    try:
        after = face.getLearnedFacesList()
    except Exception:
        after = []
    print("[reset_identity] faces before:", before)
    print("[reset_identity] faces after:", after)
    print("[reset_identity] local user cache cleared")


if __name__ == "__main__":
    main()

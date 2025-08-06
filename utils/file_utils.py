# utils/file_utils.py
# -*- coding: utf-8 -*-
"""
Helper for timestamped filenames (no f-strings).
"""

import os
import datetime

def generate_audio_filename(base_dir="./audio/", prefix="input"):
    """
    Return a path like:
      base_dir/prefix_YYYYMMDD_HHMMSS.wav
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = "{}_{}.wav".format(prefix, timestamp)
    return os.path.join(base_dir, filename)

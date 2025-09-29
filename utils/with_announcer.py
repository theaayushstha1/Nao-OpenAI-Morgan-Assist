# -*- coding: utf-8 -*-
# utils/with_announcer.py
from processing_announcer import ProcessingAnnouncer

def with_processing_announcer(tts_proxy, server_call_func, first_delay=0.7, interval=3.0):
    """
    Wraps a server call with the ProcessingAnnouncer.
    Only speaks filler if the call takes longer than first_delay.
    """
    ann = None
    try:
        ann = ProcessingAnnouncer(
            tts_say=tts_proxy.say,
            stop_all=getattr(tts_proxy, "stopAll", None),
            first_delay=first_delay,
            interval=interval
        )
        ann.start()

        # Run your server call (e.g., requests.post)
        return server_call_func()

    finally:
        try:
            if ann:
                ann.stop(interrupt=True)
        except:
            pass

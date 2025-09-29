# -*- coding: utf-8 -*-
import threading
import time
import random

class ProcessingAnnouncer(object):
    """
    Speaks after a delay ONLY if work is still ongoing.
    Will speak up to `max_utterances` times, spaced by `interval`.
    Call stop(interrupt=True) to cut any queued/ongoing TTS.
    """
    def __init__(
        self,
        tts_say,
        stop_all=None,
        first_delay=2.5,       # wait this long before saying anything
        interval=3.5,          # gap between messages if still working
        max_utterances=2,      # say at most twice
        phrases=None
    ):
        self.tts_say = tts_say
        self.stop_all = stop_all
        self.first_delay = float(first_delay)
        self.interval = float(interval)
        self.max_utterances = int(max_utterances)
        self._stop = threading.Event()
        self._thread = None
        self._phrases = phrases or [
            "One moment…",
            "analyzing your command",
            "I'm processing your request",
            "Processing at the moment…",
            "Just a sec please…",
            "please hold on…","please bare with me…",
        ]

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        t = threading.Thread(target=self._run)
        t.daemon = True
        self._thread = t
        t.start()

    def stop(self, interrupt=False):
        self._stop.set()
        if interrupt and self.stop_all:
            try:
                self.stop_all()
            except:
                pass

    def _sleep_with_checks(self, seconds):
        step = 0.05
        waited = 0.0
        while waited < seconds and not self._stop.is_set():
            time.sleep(step)
            waited += step

    def _run(self):
        # Say nothing unless work lasts longer than first_delay
        self._sleep_with_checks(self.first_delay)
        if self._stop.is_set():
            return

        said = 0
        while not self._stop.is_set() and said < self.max_utterances:
            try:
                self.tts_say(random.choice(self._phrases))
            except:
                pass
            said += 1
            if said >= self.max_utterances:
                break
            self._sleep_with_checks(self.interval)

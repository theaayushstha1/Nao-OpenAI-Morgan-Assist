# -*- coding: utf-8 -*-
import threading
import time
import random

class ProcessingAnnouncer(object):
    def __init__(self, tts_say, stop_all=None, first_delay=0.8, interval=3.0):
        self.tts_say = tts_say
        self.stop_all = stop_all
        self.first_delay = first_delay
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._phrases = [
                "Give me a moment to think.",
                "I’m working on that answer for you.",
                "Just a sec, still processing.",
                "Hang tight, I’m almost ready.",
                "Let me check that for you.",
                "One moment please.",
                "Thinking it through.",
                "I’m on it, hold on.",
                "Almost there, just a second.",
                "Processing, please wait."
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

    def _run(self):
        time.sleep(self.first_delay)
        while not self._stop.is_set():
            try:
                self.tts_say(random.choice(self._phrases))
            except:
                pass
            for _ in range(int(self.interval * 10)):
                if self._stop.is_set():
                    break
                time.sleep(0.1)

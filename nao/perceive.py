# -*- coding: utf-8 -*-
"""Watch for people entering NAO's view and invoke a callback."""
from __future__ import print_function

import time
import threading


class Watcher(object):
    """Subscribes to ALPeoplePerception and fires on_person(face_jpeg_path)."""

    def __init__(self, qi_session, camera_capture, on_person, debounce_sec=2.0):
        self.session = qi_session
        self.camera = camera_capture  # nao.utils.camera_capture module
        self.on_person = on_person
        self.debounce_sec = debounce_sec
        self._last_seen = 0.0
        self._stop = threading.Event()
        self._thread = None

    def start(self, nao_ip, nao_port=9559):
        self.nao_ip = nao_ip
        self.nao_port = nao_port
        self._stop.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        memory = self.session.service("ALMemory")
        people = self.session.service("ALPeoplePerception")
        try:
            people.subscribe("alive_mode")
        except Exception as e:
            print("[perceive] could not subscribe ALPeoplePerception:", e)
            return
        try:
            while not self._stop.is_set():
                try:
                    ids = memory.getData("PeoplePerception/PeopleList")
                except Exception:
                    ids = None
                now = time.time()
                if ids and (now - self._last_seen) > self.debounce_sec:
                    self._last_seen = now
                    img = self.camera.snap_quick(self.nao_ip, self.nao_port)
                    if img:
                        try:
                            self.on_person(img)
                        except Exception as e:
                            print("[perceive] callback error:", e)
                time.sleep(0.5)
        finally:
            try:
                people.unsubscribe("alive_mode")
            except Exception:
                pass

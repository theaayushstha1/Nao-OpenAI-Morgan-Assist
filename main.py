# -*- coding: utf-8 -*-
"""NAO entry point. Wake loop -> conversation.run(hint)."""
from __future__ import print_function

import qi

import config
import wake_listener
import conversation


def _get_phrase():
    try:
        result = wake_listener.listen_for_command(config.NAO_IP, config.NAO_PORT)
        if isinstance(result, tuple):
            return result[0] if result else None
        return result
    except Exception as e:
        print("wake error:", e)
        return None


def main():
    session = qi.Session()
    session.connect("tcp://{0}:{1}".format(config.NAO_IP, config.NAO_PORT))
    while True:
        phrase = _get_phrase()
        hint = wake_listener.extract_hint(phrase)
        try:
            conversation.run(session, initial_hint=hint)
        except KeyboardInterrupt:
            print("Exiting.")
            return
        except Exception as e:
            print("Conversation loop error:", e)


if __name__ == "__main__":
    main()

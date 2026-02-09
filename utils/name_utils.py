# -*- coding: utf-8 -*-
from __future__ import print_function
import re


def extract_name(text):
    """Extract a person's name from transcribed speech.

    Tries pattern matching first, then falls back to the first word.
    Filters out common non-name words.
    """
    if not text:
        return None
    patterns = [
        r"(?:my name is|i am|i'm|call me|this is)\s+([A-Za-z]+)",
        r"^([A-Za-z]+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, text.strip(), re.IGNORECASE)
        if m:
            name = m.group(1).capitalize()
            if name.lower() not in ["the", "a", "an", "my", "is", "am"]:
                return name
    words = text.strip().split()
    if words:
        first_word = words[0].capitalize()
        if len(first_word) > 1 and first_word.isalpha():
            return first_word
    return None

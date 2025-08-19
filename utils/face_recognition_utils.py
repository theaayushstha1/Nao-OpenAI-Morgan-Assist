# face_recognition_utils.py
# -*- coding: utf-8 -*-
"""
Detect and identify users by face.  Use 'face_recognition' library.
"""

import os
import pickle
import face_recognition
from naoqi import ALPhotoCapture

ENCODINGS_FILE = "face_encodings.pkl"

def load_encodings():
    if os.path.exists(ENCODINGS_FILE):
        with open(ENCODINGS_FILE, "rb") as f:
            return pickle.load(f)
    return {}

def save_encodings(data):
    with open(ENCODINGS_FILE, "wb") as f:
        pickle.dump(data, f)

def capture_face(robot, username):
    """
    Capture photo using NAO and return raw image array.
    """
    cam = ALPhotoCapture("capture")
    cam.setResolution(2)  # VGA
    cam.setPictureFormat("jpg")
    filename = "/home/nao/face.jpg"
    cam.takePicture("/home/nao/", "face.jpg")
    import cv2
    img = cv2.imread(filename)
    return img

def learn_face(robot, username):
    encodings = load_encodings()
    img = capture_face(robot, username)
    faces = face_recognition.face_encodings(img)
    if faces:
        encodings[username] = faces[0]
        save_encodings(encodings)
        return True
    return False

def identify_face(robot):
    encodings = load_encodings()
    img = capture_face(robot, "temp")
    faces = face_recognition.face_encodings(img)
    if faces:
        unknown = faces[0]
        names = list(encodings.keys())
        matches = face_recognition.compare_faces(
            list(encodings.values()), unknown, tolerance=0.5)
        for name, match in zip(names, matches):
            if match:
                return name
    return None

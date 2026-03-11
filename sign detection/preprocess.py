"""
Preprocessing Pipeline: Video → MediaPipe Landmarks → NumPy arrays.

Walks the dataset directory, extracts pose + hand landmarks from each video,
pads/truncates to a fixed sequence length, and saves as .npz.

Saves person ID and video index with each sample so training can do a proper
per-video split (no data leakage).

Usage:
    python preprocess.py
"""
import os
import cv2
import numpy as np
import mediapipe as mp
from tqdm import tqdm
from config import (
    DATA_DIR, PREPROCESSED_DATA_PATH,
    SEQUENCE_LENGTH, MAX_VIDEO_FRAMES, TOTAL_FEATURES,
    NUM_POSE_LANDMARKS, NUM_HAND_LANDMARKS, FEATURES_PER_LANDMARK,
    MP_STATIC_IMAGE_MODE, MP_MIN_DETECTION_CONFIDENCE, MP_MIN_TRACKING_CONFIDENCE,
)


def init_mediapipe():
    """Initialize MediaPipe Holistic model."""
    mp_holistic = mp.solutions.holistic
    holistic = mp_holistic.Holistic(
        static_image_mode=MP_STATIC_IMAGE_MODE,
        min_detection_confidence=MP_MIN_DETECTION_CONFIDENCE,
        min_tracking_confidence=MP_MIN_TRACKING_CONFIDENCE,
    )
    return holistic


def extract_landmarks(results):
    """
    Extract a flat feature vector from MediaPipe Holistic results.
    Returns a 1D array of shape (TOTAL_FEATURES,).
    """
    features = []

    if results.pose_landmarks:
        for lm in results.pose_landmarks.landmark:
            features.extend([lm.x, lm.y, lm.z])
    else:
        features.extend([0.0] * NUM_POSE_LANDMARKS * FEATURES_PER_LANDMARK)

    if results.left_hand_landmarks:
        for lm in results.left_hand_landmarks.landmark:
            features.extend([lm.x, lm.y, lm.z])
    else:
        features.extend([0.0] * NUM_HAND_LANDMARKS * FEATURES_PER_LANDMARK)

    if results.right_hand_landmarks:
        for lm in results.right_hand_landmarks.landmark:
            features.extend([lm.x, lm.y, lm.z])
    else:
        features.extend([0.0] * NUM_HAND_LANDMARKS * FEATURES_PER_LANDMARK)

    return np.array(features, dtype=np.float32)


def process_video(video_path, holistic):
    """
    Process a single video file and return a sequence of landmark frames.
    Returns None if video cannot be read or is too long.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    frames = []
    frame_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1
        if frame_count > MAX_VIDEO_FRAMES:
            cap.release()
            return None

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = holistic.process(rgb)
        landmarks = extract_landmarks(results)
        frames.append(landmarks)

    cap.release()

    if len(frames) == 0:
        return None

    return np.array(frames)


def pad_or_truncate(sequence, target_length):
    """Pad with zeros or truncate a sequence to a fixed length."""
    if len(sequence) >= target_length:
        indices = np.linspace(0, len(sequence) - 1, target_length, dtype=int)
        return sequence[indices]
    else:
        pad_length = target_length - len(sequence)
        padding = np.zeros((pad_length, sequence.shape[1]), dtype=np.float32)
        return np.vstack([sequence, padding])


def collect_video_paths():
    """
    Walk the dataset and collect (video_path, label, person_id) tuples.
    """
    samples = []
    categories = ["Nouns", "Verbs"]

    for category in categories:
        category_dir = os.path.join(DATA_DIR, category)
        if not os.path.isdir(category_dir):
            print(f"[WARNING] Category dir not found: {category_dir}")
            continue

        for person in sorted(os.listdir(category_dir)):
            person_dir = os.path.join(category_dir, person)
            if not os.path.isdir(person_dir):
                continue

            for filename in sorted(os.listdir(person_dir)):
                if not filename.lower().endswith(".mp4"):
                    continue
                video_path = os.path.join(person_dir, filename)
                label = os.path.splitext(filename)[0]
                person_id = person
                samples.append((video_path, label, person_id))

    return samples


def main():
    print("=" * 60)
    print("  Sign Language Dataset Preprocessing")
    print("=" * 60)

    samples = collect_video_paths()
    print(f"\nFound {len(samples)} video files")

    unique_labels = sorted(set(label for _, label, _ in samples))
    unique_persons = sorted(set(pid for _, _, pid in samples))
    print(f"Unique signs (classes): {len(unique_labels)}")
    print(f"Persons: {unique_persons}")

    # Find classes present in ALL persons for better training
    class_person_map = {}
    for _, label, pid in samples:
        if label not in class_person_map:
            class_person_map[label] = set()
        class_person_map[label].add(pid)

    common_classes = [lbl for lbl, pids in class_person_map.items()
                      if len(pids) == len(unique_persons)]
    print(f"Classes in ALL {len(unique_persons)} persons: {len(common_classes)}")

    label_to_idx = {label: idx for idx, label in enumerate(unique_labels)}

    holistic = init_mediapipe()

    X_all = []
    y_all = []
    person_ids = []
    video_ids = []  # Unique ID per video for splitting
    skipped = 0

    print("\nExtracting landmarks from videos...")
    for vid_idx, (video_path, label, person_id) in enumerate(
            tqdm(samples, desc="Processing videos")):
        raw_sequence = process_video(video_path, holistic)

        if raw_sequence is None:
            skipped += 1
            continue

        sequence = pad_or_truncate(raw_sequence, SEQUENCE_LENGTH)
        X_all.append(sequence)
        y_all.append(label_to_idx[label])
        person_ids.append(person_id)
        video_ids.append(vid_idx)

    holistic.close()

    if len(X_all) == 0:
        print("[ERROR] No data was extracted!")
        return

    X = np.array(X_all, dtype=np.float32)
    y = np.array(y_all, dtype=np.int32)
    labels_array = np.array(unique_labels)
    persons_array = np.array(person_ids)
    video_ids_array = np.array(video_ids, dtype=np.int32)

    print(f"\n{'─' * 40}")
    print(f"  Results:")
    print(f"  Total samples: {X.shape[0]}")
    print(f"  Sequence shape: {X.shape[1:]}")
    print(f"  Classes: {len(unique_labels)}")
    print(f"  Common classes (all persons): {len(common_classes)}")
    print(f"  Skipped videos: {skipped}")
    for pid in unique_persons:
        count = np.sum(persons_array == pid)
        print(f"    {pid}: {count} samples")
    print(f"{'─' * 40}")

    np.savez_compressed(
        PREPROCESSED_DATA_PATH,
        X=X, y=y,
        labels=labels_array,
        persons=persons_array,
        video_ids=video_ids_array,
    )
    print(f"\nSaved to: {PREPROCESSED_DATA_PATH}")
    file_size_mb = os.path.getsize(PREPROCESSED_DATA_PATH) / (1024 * 1024)
    print(f"File size: {file_size_mb:.1f} MB")


if __name__ == "__main__":
    main()

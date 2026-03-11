"""
Model Training: Prototypical Network for Few-Shot Sign Language Recognition.

Instead of traditional classification (which fails with 382 classes & 2-3 
samples each), this uses a Prototypical Network that learns an EMBEDDING SPACE.

How it works:
  1. Train an encoder to map landmark sequences → compact embeddings
  2. For each class, compute a "prototype" (mean embedding of all examples)
  3. Classify new samples by finding the nearest prototype (cosine similarity)

This approach is specifically designed for many-classes-with-few-examples.

Usage:
    python train.py
"""
import os
import numpy as np
import warnings
warnings.filterwarnings('ignore', category=UserWarning)

from config import (
    PREPROCESSED_DATA_PATH, MODEL_DIR,
    SEQUENCE_LENGTH, TOTAL_FEATURES,
    LSTM_UNITS_2, DENSE_UNITS, DROPOUT_RATE,
    LEARNING_RATE, BATCH_SIZE, EPOCHS,
    EARLY_STOP_PATIENCE, REDUCE_LR_PATIENCE, REDUCE_LR_FACTOR,
)


# ──────────────────────────────────────────────
# Augmentation
# ──────────────────────────────────────────────

def augment_sample(x):
    """Augment a single sample with random transforms."""
    aug = x.copy()

    # Random noise
    aug += np.random.normal(0, 0.005, aug.shape).astype(np.float32)

    # Random temporal shift
    shift = np.random.randint(-2, 3)
    if shift > 0:
        aug = np.vstack([np.zeros((shift, aug.shape[1]), dtype=np.float32), aug[:-shift]])
    elif shift < 0:
        aug = np.vstack([aug[-shift:], np.zeros((-shift, aug.shape[1]), dtype=np.float32)])

    # Random scale
    scale = np.random.uniform(0.9, 1.1)
    aug = aug * scale

    return aug


def augment_mirror(x):
    """Mirror landmarks: flip x-coordinates, swap left/right hands."""
    aug = x.copy()
    aug[:, 0::3] = 1.0 - aug[:, 0::3]  # Flip x coords
    left = aug[:, 99:162].copy()
    right = aug[:, 162:225].copy()
    aug[:, 99:162] = right
    aug[:, 162:225] = left
    return aug


# ──────────────────────────────────────────────
# Custom Layers (avoid Lambda for serialization)
# ──────────────────────────────────────────────

import tensorflow as tf

@tf.keras.utils.register_keras_serializable()
class ReduceSumLayer(tf.keras.layers.Layer):
    """Reduce sum along axis 1 (replaces Lambda)."""
    def call(self, x):
        return tf.reduce_sum(x, axis=1)

@tf.keras.utils.register_keras_serializable()
class L2NormalizeLayer(tf.keras.layers.Layer):
    """L2 normalize along axis 1 (replaces Lambda)."""
    def call(self, x):
        return tf.math.l2_normalize(x, axis=1)


# ──────────────────────────────────────────────
# Prototypical Network
# ──────────────────────────────────────────────

def build_encoder(embedding_dim=128):
    """Build the embedding encoder network."""
    from tensorflow.keras import layers, models, regularizers

    inputs = layers.Input(shape=(SEQUENCE_LENGTH, TOTAL_FEATURES))

    x = layers.LayerNormalization()(inputs)

    # Conv1D feature extraction
    x = layers.Conv1D(256, kernel_size=3, padding='same', activation='relu')(x)
    x = layers.BatchNormalization()(x)
    x = layers.Dropout(0.2)(x)

    x = layers.Conv1D(128, kernel_size=3, padding='same', activation='relu')(x)
    x = layers.BatchNormalization()(x)
    x = layers.Dropout(0.2)(x)

    x = layers.Conv1D(64, kernel_size=5, padding='same', activation='relu')(x)
    x = layers.BatchNormalization()(x)
    x = layers.Dropout(0.2)(x)

    # Bidirectional GRU
    x = layers.Bidirectional(
        layers.GRU(LSTM_UNITS_2, return_sequences=True)
    )(x)
    x = layers.Dropout(0.2)(x)

    # Temporal attention pooling
    att = layers.Dense(1, activation='tanh')(x)
    att = layers.Flatten()(att)
    att = layers.Activation('softmax')(att)
    att = layers.RepeatVector(LSTM_UNITS_2 * 2)(att)  # *2 for bidirectional
    att = layers.Permute([2, 1])(att)
    x = layers.Multiply()([x, att])
    x = ReduceSumLayer()(x)

    # Embedding projection
    x = layers.Dense(DENSE_UNITS, activation='relu')(x)
    x = layers.BatchNormalization()(x)
    x = layers.Dropout(0.2)(x)

    # L2-normalized embedding (for cosine similarity)
    x = layers.Dense(embedding_dim)(x)
    embeddings = L2NormalizeLayer()(x)

    model = models.Model(inputs, embeddings, name='encoder')
    return model


def build_protonet(encoder, num_classes, embedding_dim=128):
    """
    Build a Prototypical Network for training.
    Uses the encoder + a classification layer with prototype-based loss.
    """
    import tensorflow as tf
    from tensorflow.keras import layers, models

    inputs = layers.Input(shape=(SEQUENCE_LENGTH, TOTAL_FEATURES))
    embeddings = encoder(inputs)

    # For training, we add a cosine similarity classifier
    # This helps learn good embeddings faster than pure episode training
    classifier = layers.Dense(
        num_classes, activation='softmax', use_bias=False,
        name='proto_classifier'
    )(embeddings)

    model = models.Model(inputs, classifier, name='protonet')
    return model


def generate_episodes(X, y, n_way=20, k_shot=2, q_query=1, n_episodes=200):
    """
    Generate training episodes for prototypical network.
    Each episode: select n_way classes, k_shot support + q_query query per class.
    """
    classes = np.unique(y)
    class_indices = {c: np.where(y == c)[0] for c in classes}

    # Filter classes with enough samples
    valid_classes = [c for c in classes if len(class_indices[c]) >= k_shot + q_query]

    if len(valid_classes) < n_way:
        n_way = max(5, len(valid_classes))

    episodes_support_X = []
    episodes_support_y = []
    episodes_query_X = []
    episodes_query_y = []

    for _ in range(n_episodes):
        selected_classes = np.random.choice(valid_classes, n_way, replace=False)

        support_X, support_y = [], []
        query_X, query_y = [], []

        for i, cls in enumerate(selected_classes):
            indices = np.random.choice(class_indices[cls],
                                       k_shot + q_query, replace=True)
            for idx in indices[:k_shot]:
                support_X.append(X[idx])
                support_y.append(i)  # Re-index within episode
            for idx in indices[k_shot:]:
                query_X.append(X[idx])
                query_y.append(i)

        episodes_support_X.append(np.array(support_X))
        episodes_support_y.append(np.array(support_y))
        episodes_query_X.append(np.array(query_X))
        episodes_query_y.append(np.array(query_y))

    return (episodes_support_X, episodes_support_y,
            episodes_query_X, episodes_query_y)


def prototypical_loss(encoder, support_X, support_y, query_X, query_y, n_way):
    """Compute prototypical network loss for one episode."""
    import tensorflow as tf

    # Embed support and query
    support_emb = encoder(support_X, training=True)
    query_emb = encoder(query_X, training=True)

    # Compute prototypes (mean embedding per class)
    prototypes = []
    for i in range(n_way):
        mask = tf.equal(support_y, i)
        class_emb = tf.boolean_mask(support_emb, mask)
        prototype = tf.reduce_mean(class_emb, axis=0)
        prototypes.append(prototype)
    prototypes = tf.stack(prototypes)  # (n_way, embed_dim)

    # Cosine similarity between queries and prototypes
    # Both are L2-normalized, so dot product = cosine similarity
    logits = tf.matmul(query_emb, prototypes, transpose_b=True) * 10.0  # temperature

    loss = tf.reduce_mean(
        tf.keras.losses.sparse_categorical_crossentropy(query_y, tf.nn.softmax(logits))
    )
    preds = tf.argmax(logits, axis=1, output_type=tf.int32)
    accuracy = tf.reduce_mean(tf.cast(tf.equal(preds, query_y), tf.float32))

    return loss, accuracy


# ──────────────────────────────────────────────
# Main Training
# ──────────────────────────────────────────────

def main():
    import tensorflow as tf

    print("=" * 60)
    print("  Prototypical Network Training")
    print("  (Few-Shot Learning for Sign Language)")
    print("=" * 60)

    if not os.path.exists(PREPROCESSED_DATA_PATH):
        print(f"[ERROR] Run `python preprocess.py` first.")
        return

    data = np.load(PREPROCESSED_DATA_PATH, allow_pickle=True)
    X = data['X']
    y = data['y']
    labels = data['labels']
    persons = data['persons']

    print(f"\nData loaded: {X.shape[0]} samples, {len(labels)} classes")

    num_classes = len(labels)

    # ── Augment ALL Data ───────────────────────────────
    # Since we're doing few-shot, every sample matters.
    # Augment to create more variety in the embedding space.
    print("\n  Augmenting dataset...")
    X_augmented = [X]
    y_augmented = [y]

    for i in range(8):
        X_aug = np.array([augment_sample(x) for x in X])
        X_augmented.append(X_aug)
        y_augmented.append(y)

    # Add mirrored versions
    for i in range(4):
        X_mirror = np.array([augment_mirror(augment_sample(x)) for x in X])
        X_augmented.append(X_mirror)
        y_augmented.append(y)

    X_all = np.concatenate(X_augmented, axis=0).astype(np.float32)
    y_all = np.concatenate(y_augmented, axis=0).astype(np.int32)

    # Shuffle
    idx = np.random.permutation(len(X_all))
    X_all = X_all[idx]
    y_all = y_all[idx]

    print(f"  Augmented: {X_all.shape[0]} samples ({X_all.shape[0] / len(X):.0f}x)")

    # ── Phase 1: Pre-train encoder with classification ─
    print(f"\n{'─' * 40}")
    print("  Phase 1: Pre-training encoder (classification)")
    print(f"{'─' * 40}\n")

    EMBED_DIM = 128
    encoder = build_encoder(EMBED_DIM)
    protonet = build_protonet(encoder, num_classes, EMBED_DIM)

    protonet.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss='sparse_categorical_crossentropy',
        metrics=['accuracy'],
    )

    os.makedirs(MODEL_DIR, exist_ok=True)

    # Use all data for pre-training (we evaluate with few-shot later)
    protonet.fit(
        X_all, y_all,
        epochs=30,
        batch_size=64,
        verbose=1,
        callbacks=[
            tf.keras.callbacks.ReduceLROnPlateau(
                monitor='loss', factor=0.5, patience=5, min_lr=1e-6, verbose=1
            ),
        ]
    )

    # ── Phase 2: Fine-tune with episodic training ──────
    print(f"\n{'─' * 40}")
    print("  Phase 2: Episodic fine-tuning (prototypical loss)")
    print(f"{'─' * 40}\n")

    optimizer = tf.keras.optimizers.Adam(learning_rate=0.0003)
    best_acc = 0.0

    for epoch in range(50):
        # Generate episodes from augmented data
        n_way = min(30, num_classes)
        episodes = generate_episodes(X_all, y_all, n_way=n_way,
                                     k_shot=3, q_query=2, n_episodes=100)
        sup_X, sup_y, qry_X, qry_y = episodes

        epoch_losses = []
        epoch_accs = []

        for ep_idx in range(len(sup_X)):
            with tf.GradientTape() as tape:
                loss, acc = prototypical_loss(
                    encoder,
                    tf.constant(sup_X[ep_idx]),
                    tf.constant(sup_y[ep_idx]),
                    tf.constant(qry_X[ep_idx]),
                    tf.constant(qry_y[ep_idx]),
                    n_way
                )

            grads = tape.gradient(loss, encoder.trainable_variables)
            optimizer.apply_gradients(zip(grads, encoder.trainable_variables))

            epoch_losses.append(loss.numpy())
            epoch_accs.append(acc.numpy())

        mean_loss = np.mean(epoch_losses)
        mean_acc = np.mean(epoch_accs)

        if mean_acc > best_acc:
            best_acc = mean_acc
            encoder.save(os.path.join(MODEL_DIR, "encoder.keras"))

        if (epoch + 1) % 5 == 0:
            print(f"  Epoch {epoch+1:3d}/50 — Loss: {mean_loss:.4f}, "
                  f"Acc: {mean_acc:.4f} ({n_way}-way), Best: {best_acc:.4f}")

    # ── Compute & Save Class Prototypes ────────────────
    print(f"\n{'─' * 40}")
    print("  Computing class prototypes...")
    print(f"{'─' * 40}\n")

    # Load best encoder
    encoder = tf.keras.models.load_model(
        os.path.join(MODEL_DIR, "encoder.keras"), safe_mode=False,
        custom_objects={'ReduceSumLayer': ReduceSumLayer, 'L2NormalizeLayer': L2NormalizeLayer}
    )

    # Compute prototypes using ORIGINAL (non-augmented) data
    prototypes = np.zeros((num_classes, EMBED_DIM), dtype=np.float32)
    class_counts = np.zeros(num_classes, dtype=np.int32)

    # Use original + a few augmented versions for robust prototypes
    proto_X = [X]
    for _ in range(4):
        proto_X.append(np.array([augment_sample(x) for x in X]))
    proto_X = np.concatenate(proto_X, axis=0)
    proto_y = np.tile(y, 5)

    # Embed all samples
    embeddings = encoder.predict(proto_X, batch_size=64, verbose=0)

    for i in range(len(proto_y)):
        cls = proto_y[i]
        prototypes[cls] += embeddings[i]
        class_counts[cls] += 1

    # Average and normalize
    for c in range(num_classes):
        if class_counts[c] > 0:
            prototypes[c] /= class_counts[c]
            prototypes[c] /= (np.linalg.norm(prototypes[c]) + 1e-8)

    # Save prototypes
    np.save(os.path.join(MODEL_DIR, "prototypes.npy"), prototypes)
    np.save(os.path.join(MODEL_DIR, "label_encoder.npy"), labels)

    print(f"  Prototypes computed for {np.sum(class_counts > 0)} classes")

    # ── Evaluate: Few-Shot Accuracy ────────────────────
    print(f"\n{'─' * 40}")
    print("  Evaluation (all original samples)")
    print(f"{'─' * 40}\n")

    # Evaluate on original data
    orig_embeddings = encoder.predict(X, batch_size=64, verbose=0)

    # Cosine similarity to prototypes
    similarities = orig_embeddings @ prototypes.T  # (N, num_classes)

    top1 = 0
    top5 = 0
    top10 = 0

    for i in range(len(y)):
        ranked = np.argsort(similarities[i])[::-1]
        if y[i] == ranked[0]:
            top1 += 1
        if y[i] in ranked[:5]:
            top5 += 1
        if y[i] in ranked[:10]:
            top10 += 1

    n = len(y)
    print(f"  Top-1 Accuracy:  {top1/n:.4f} ({top1}/{n})")
    print(f"  Top-5 Accuracy:  {top5/n:.4f} ({top5}/{n})")
    print(f"  Top-10 Accuracy: {top10/n:.4f} ({top10}/{n})")

    # Per-person evaluation
    for pid in sorted(set(persons)):
        mask = persons == pid
        p_sims = orig_embeddings[mask] @ prototypes.T
        p_y = y[mask]
        p_top1 = sum(1 for i in range(len(p_y))
                     if p_y[i] == np.argmax(p_sims[i]))
        p_top5 = sum(1 for i in range(len(p_y))
                     if p_y[i] in np.argsort(p_sims[i])[-5:])
        print(f"    {pid}: Top-1={p_top1/len(p_y):.4f}, "
              f"Top-5={p_top5/len(p_y):.4f} ({len(p_y)} samples)")

    # ── Summary ────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print(f"  RESULTS SUMMARY — Prototypical Network")
    print(f"{'═' * 60}")
    print(f"  Top-1 Accuracy:  {top1/n:.4f}")
    print(f"  Top-5 Accuracy:  {top5/n:.4f}")
    print(f"  Top-10 Accuracy: {top10/n:.4f}")
    print(f"  Episodic Best:   {best_acc:.4f} ({n_way}-way accuracy)")
    print(f"{'═' * 60}")
    print(f"\n  Saved:")
    print(f"    Encoder:    {os.path.join(MODEL_DIR, 'encoder.keras')}")
    print(f"    Prototypes: {os.path.join(MODEL_DIR, 'prototypes.npy')}")
    print(f"    Labels:     {os.path.join(MODEL_DIR, 'label_encoder.npy')}")


if __name__ == "__main__":
    main()

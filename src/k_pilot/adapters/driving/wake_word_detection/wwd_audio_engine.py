from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import librosa
import numpy as np
import sounddevice as sd
import soundfile as sf
import torch
from numpy.typing import NDArray
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from k_pilot.core.shared.logging import get_logger

if TYPE_CHECKING:
    from torch import Tensor

    # Silero VAD types are dynamic, kept minimal
    type VadModel = Any  # type: ignore
    type SpeechTimestamp = dict[str, int]


logger: Final = get_logger(__name__)

# Module-level singleton for lazy-loaded VAD model
_vad_model_instance: VadModel | None = None

# Audio constants
DEFAULT_SAMPLE_RATE: Final = 16_000
MIN_SAMPLES_FOR_FEATURES: Final = 16_000  # 1 second at 16kHz
MAX_DTW_DISTANCE: Final = 999.0


class AudioError(Exception):
    """Base exception for audio processing failures."""

    pass


class FeatureExtractionError(AudioError):
    """Failed to extract embeddings from audio."""

    pass


class ModelLoadError(AudioError):
    """Failed to load ML model."""

    pass


@dataclass(frozen=True, slots=True)
class AudioConfig:
    """Immutable configuration for audio operations."""

    sample_rate: int = DEFAULT_SAMPLE_RATE
    min_samples: int = MIN_SAMPLES_FOR_FEATURES


@dataclass(frozen=True, slots=True)
class DriftMetrics:
    """Statistical metrics for concept drift detection."""

    mean: float
    percentile_90: float

    def is_healthy(self, threshold: float = 0.5) -> bool:
        """Determine if the system is experiencing acceptable drift."""
        return self.mean < threshold and self.percentile_90 < threshold * 1.5


def get_vad_model() -> VadModel:
    """Lazy-load and cache the Silero VAD model.

    Returns:
        The cached VAD model instance.

    Raises:
        ModelLoadError: If the model cannot be loaded.
    """
    global _vad_model_instance

    if _vad_model_instance is not None:
        return _vad_model_instance

    try:
        logger.info("Loading Silero VAD model...")
        # Dynamic import to handle potential missing dependencies gracefully
        from silero_vad import load_silero_vad

        _vad_model_instance = load_silero_vad()
        logger.info("VAD model loaded successfully")
        return _vad_model_instance

    except Exception as exc:
        raise ModelLoadError(f"Failed to load Silero VAD: {exc}") from exc


def trim_silence(
    audio: NDArray[np.float32], sample_rate: int = DEFAULT_SAMPLE_RATE
) -> NDArray[np.float32]:
    """Remove leading and trailing silence using VAD.

    Args:
        audio: Input audio array.
        sample_rate: Sampling rate in Hz.

    Returns:
        Audio with silence trimmed, or original if no speech detected.
    """
    if audio.size == 0:
        return audio

    tensor_audio: Tensor = torch.from_numpy(audio).float()
    model = get_vad_model()

    # Import here to avoid module-level dependency issues
    from silero_vad import get_speech_timestamps

    timestamps: list[SpeechTimestamp] = get_speech_timestamps(
        tensor_audio, model, sampling_rate=sample_rate
    )

    if not timestamps:
        return audio

    start = timestamps[0]["start"]
    end = timestamps[-1]["end"]

    return audio[start:end]


def pad_audio(
    audio: NDArray[np.float32], min_samples: int = MIN_SAMPLES_FOR_FEATURES
) -> NDArray[np.float32]:
    """Center-pad audio with zeros if shorter than minimum.

    Args:
        audio: Input audio array.
        min_samples: Target length in samples.

    Returns:
        Padded audio array of at least min_samples length.
    """
    if len(audio) >= min_samples:
        return audio

    pad_total = min_samples - len(audio)
    pad_left = pad_total // 2
    pad_right = pad_total - pad_left

    return np.pad(audio, (pad_left, pad_right), mode="constant", constant_values=0)


def save_audio(
    audio: NDArray[np.float32], path: str | Path, sample_rate: int = DEFAULT_SAMPLE_RATE
) -> None:
    """Save audio array to disk as WAV file.

    Args:
        audio: Audio data to save.
        path: Destination file path.
        sample_rate: Sampling rate in Hz.

    Raises:
        AudioError: If writing fails.
    """
    try:
        sf.write(str(path), audio, samplerate=sample_rate)
        logger.debug("Audio saved", path=str(path), samples=len(audio))
    except Exception as exc:
        raise AudioError(f"Failed to save audio to {path}: {exc}") from exc


def play_feedback_sound(path: str | Path) -> None:
    """Play a confirmation beep or feedback sound.

    Args:
        path: Path to audio file.

    Note:
        Failures are logged but not raised (non-critical feedback).
    """
    try:
        data, fs = sf.read(str(path))
        sd.play(data, fs)
        sd.wait()
    except FileNotFoundError:
        logger.warning("Feedback sound not found", path=str(path))
    except Exception as exc:
        logger.warning("Failed to play feedback sound", path=str(path), error=str(exc))


def compute_normalized_dtw(
    features_a: NDArray[np.float32], features_b: NDArray[np.float32]
) -> float:
    """Compute DTW distance normalized by warp path length.

    Args:
        features_a: First feature matrix (features × time).
        features_b: Second feature matrix (features × time).

    Returns:
        Normalized DTW distance (lower is more similar).
    """
    cost_matrix, warp_path = librosa.sequence.dtw(X=features_a, Y=features_b, metric="cosine")

    total_cost = float(cost_matrix[-1, -1])
    normalization = len(warp_path) or 1  # Avoid div by zero

    return total_cost / normalization


def load_golden_embeddings(directory: Path) -> dict[str, NDArray[np.float32]]:
    """Load reference audio files and convert to feature embeddings.

    Args:
        directory: Path containing .wav reference files.

    Returns:
        Mapping of filename to feature array.

    Raises:
        FeatureExtractionError: If lwake features cannot be imported.
    """
    if not directory.exists():
        logger.warning("Golden records directory does not exist", path=str(directory))
        return {}

    embeddings: dict[str, NDArray[np.float32]] = {}

    try:
        from lwake.features import extract_embedding_features
    except ImportError as exc:
        raise FeatureExtractionError(f"lwake features module not available: {exc}") from exc

    for wav_file in directory.glob("*.wav"):
        try:
            features = extract_embedding_features(path=str(wav_file))

            if features is None:
                logger.warning("No features extracted", file=wav_file.name)
                continue

            embeddings[wav_file.name] = features

        except Exception as exc:
            logger.error("Failed to load golden record", file=wav_file.name, error=str(exc))
            continue

    logger.info("Loaded golden records", count=len(embeddings), directory=str(directory))
    return embeddings


def compare_audio_to_references(
    audio: NDArray[np.float32],
    reference_embeddings: dict[str, NDArray[np.float32]],
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> tuple[str | None, float]:
    """Compare live audio against reference embeddings using DTW.

    Args:
        audio: Input audio array.
        reference_embeddings: Dictionary of reference feature matrices.
        sample_rate: Audio sampling rate.

    Returns:
        Tuple of (best_matching_filename, normalized_distance).
        Returns (None, MAX_DTW_DISTANCE) on failure.
    """
    if not reference_embeddings:
        logger.warning("No reference embeddings provided for comparison")
        return None, MAX_DTW_DISTANCE

    try:
        from lwake.features import extract_embedding_features

        padded_audio = pad_audio(audio)
        live_features = extract_embedding_features(y=padded_audio, sample_rate=sample_rate)

        if live_features is None:
            logger.error("Feature extraction returned None")
            return None, MAX_DTW_DISTANCE

    except Exception as exc:
        logger.error("Feature extraction failed", error=str(exc))
        return None, MAX_DTW_DISTANCE

    best_match: str | None = None
    best_distance: float = MAX_DTW_DISTANCE

    for name, ref_features in reference_embeddings.items():
        distance = compute_normalized_dtw(live_features, ref_features)

        if distance < best_distance:
            best_distance = distance
            best_match = name

    logger.debug(
        "DTW comparison complete",
        match=best_match,
        distance=best_distance,
        references=len(reference_embeddings),
    )

    return best_match, best_distance


def calculate_concept_drift(
    baseline: dict[str, NDArray[np.float32]],
    learned: dict[str, NDArray[np.float32]],
) -> DriftMetrics:
    """Calculate drift between baseline and learned embeddings.

    Computes mean and 90th percentile of all pairwise DTW distances
    to audit system health and detect model degradation.

    Args:
        baseline: Original reference embeddings.
        learned: Newly learned embeddings.

    Returns:
        DriftMetrics containing statistical measures.
    """
    if not baseline or not learned:
        return DriftMetrics(mean=0.0, percentile_90=0.0)

    distances: list[float] = []

    for learned_features in learned.values():
        for baseline_features in baseline.values():
            dist = compute_normalized_dtw(learned_features, baseline_features)
            distances.append(dist)

    if not distances:
        return DriftMetrics(mean=0.0, percentile_90=0.0)

    mean_drift = float(np.mean(distances))
    p90_drift = float(np.percentile(distances, 90))

    return DriftMetrics(mean=mean_drift, percentile_90=p90_drift)


def _compute_max_intra_group_distance(
    group_names: list[str], embeddings: dict[str, NDArray[np.float32]]
) -> float:
    """
    Función auxiliar pura: Calcula la distancia DTW máxima entre todos
    los audios de un mismo grupo.

    Args:
        group_names: Lista de nombres de archivo que conforman el grupo.
        embeddings: Diccionario completo de características.

    Returns:
        La mayor distancia encontrada, o 0.0 si hay menos de 2 audios.
    """
    if len(group_names) < 2:
        return 0.0

    distancias = [
        compute_normalized_dtw(embeddings[group_names[i]], embeddings[group_names[j]])
        for i in range(len(group_names))
        for j in range(i + 1, len(group_names))
    ]
    return float(np.max(distancias))


def _generate_clustering_curve(embeddings: dict[str, NDArray[np.float32]]) -> list[dict[str, Any]]:
    """
    Función auxiliar pura: Muestrea el comportamiento del clustering
    barriendo el valor de tightness.

    Returns:
        Una lista de diccionarios que representa la curva de crecimiento
        del grupo principal (Moda).
    """
    curve = []
    for t in np.arange(0.05, 0.41, 0.01):
        grupos = cluster_embeddings(embeddings, tightness=float(t))

        # Lógica original: Siempre evaluamos sobre el grupo más grande
        grupo_principal = grupos[0]
        max_d = _compute_max_intra_group_distance(grupo_principal, embeddings)

        curve.append(
            {
                "t": float(t),
                "size": len(grupo_principal),
                "max_dist": max_d,
                "group": grupo_principal,
            }
        )
    return curve


def _analyze_curvature_and_find_elbow(
    curve: list[dict[str, Any]], n_total: int
) -> tuple[int, NDArray[np.float64]]:
    """
    Función auxiliar: Aplica cálculo diferencial (1ra y 2da derivada) para
    encontrar el 'codo' matemático donde el crecimiento del grupo se estabiliza.

    Returns:
        El índice (best_idx) del punto óptimo en la curva y el array de la 2da derivada.
    """
    # 1. Preparar vectores normalizados
    sizes = np.array([c["size"] for c in curve])
    t_values = np.array([c["t"] for c in curve])

    y = sizes / n_total  # Normalizar tamaño (0 a 1)
    x = t_values  # Valores de tightness (0.05 a 0.40)

    # 2. Calcular derivadas por diferencias finitas
    dy = np.gradient(y, x)  # Velocidad de crecimiento
    d2y = np.gradient(dy, x)  # Aceleración/Curvatura

    # 3. Determinar el límite de distancia aceptable según volumen de datos
    max_allowed_dist = 0.28 if n_total > 20 else (0.22 if n_total > 10 else 0.18)

    best_idx = 0
    max_curvature = -999.0

    # 4. Buscar el punto de máxima inflexión negativa (el codo)
    for i in range(1, len(curve) - 1):
        curvature = -d2y[i]  # Buscamos inflexión negativa (estabilización)

        # Ignorar puntos que ya superaron el límite de distancia segura
        if curve[i]["max_dist"] > max_allowed_dist:
            continue

        # Bonificar puntos que cubren una mayor parte del dataset
        coverage_bonus = y[i] * 0.5
        score = curvature + coverage_bonus

        if score > max_curvature:
            max_curvature = score
            best_idx = i

    # 5. Fallback: Si la derivada falla, usar ratio de eficiencia (tamaño / costo_distancia)
    if best_idx == 0:
        best_idx = max(
            range(len(curve)), key=lambda i: curve[i]["size"] / (curve[i]["max_dist"] + 0.01)
        )

    return best_idx, d2y


def cluster_embeddings(
    embeddings: dict[str, NDArray[np.float32]], tightness: float = 0.20
) -> list[list[str]]:
    """
    Agrupa los audios usando clustering jerárquico.

    Args:
        embeddings: Diccionario con nombres de archivo y sus matrices de características.
        tightness: Umbral de distancia para cortar el árbol (menor = grupos más apretados/estrictos).

    Returns:
        Lista de grupos (cada grupo es una lista de nombres de archivo),
        ordenados del grupo más grande al más pequeño.
    """
    names = list(embeddings.keys())
    records = list(embeddings.values())
    n = len(names)

    if n < 2:
        return [names]

    # 1. Matriz de distancias exacta
    matriz = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            dist = compute_normalized_dtw(records[i], records[j])
            matriz[i, j] = dist
            matriz[j, i] = dist

    # 2. Agrupación Jerárquica
    distancias_condensadas = squareform(matriz)
    Z = linkage(distancias_condensadas, method="average")
    etiquetas = fcluster(Z, t=tightness, criterion="distance")

    # 3. Mapear etiquetas a nombres de archivos
    grupos = defaultdict(list)
    for name, etiqueta in zip(names, etiquetas):
        grupos[etiqueta].append(name)

    # Devolver grupos ordenados por tamaño (el que tiene más audios es el índice 0)
    return [grupo for _, grupo in sorted(grupos.items(), key=lambda x: len(x[1]), reverse=True)]


def find_optimal_tightness(
    embeddings: dict[str, NDArray[np.float32]], logger: Any
) -> tuple[list[str], float, float]:
    """
    Orquesta el análisis de calibración: genera la curva de agrupaciones y usa
    la segunda derivada para encontrar el tightness óptimo autónomamente.
    """
    n_total = len(embeddings)

    # Bypass para datasets minúsculos
    if n_total < 3:
        all_names = list(embeddings.keys())
        max_d = _compute_max_intra_group_distance(all_names, embeddings)
        return all_names, (max_d if max_d > 0 else 0.15), 0.40

    # 1. Generar la curva
    curve = _generate_clustering_curve(embeddings)

    # 2. Analizar las derivadas
    best_idx, d2y = _analyze_curvature_and_find_elbow(curve, n_total)
    winner = curve[best_idx]

    # 3. Log de resultados para depuración
    logger.info("Curva de clustering (tightness → size):")
    for i, c in enumerate(curve):
        mark = " <--" if i == best_idx else ""
        logger.info(
            f"  t={c['t']:.2f}: size={c['size']:3d}, dist={c['max_dist']:.3f}, "
            f"d2y={d2y[i]:+.3f}{mark}"
        )

    return winner["group"], winner["max_dist"], winner["t"]


def calculate_dynamic_thresholds(
    embeddings: dict[str, NDArray[np.float32]], logger: Any
) -> tuple[float, float]:
    """
    Calcula los umbrales operativos combinando la adaptabilidad orgánica de la curva
    (derivadas) con un Firewall (límites de seguridad duros).
    """
    if len(embeddings) < 2:
        return 0.12, 0.18  # pq me salio de la polla

    # 1. Inteligencia Adaptativa: Que el algoritmo orgánico haga su magia
    grupo_principal, max_base_dist, optimal_tightness = find_optimal_tightness(embeddings, logger)

    if not grupo_principal:
        logger.warning("CRÍTICO: No se pudo formar un grupo consistente. Usando fallbacks.")
        max_base_dist = 0.15  # F en el chat

    # 2. Cálculo orgánico
    organic_safe = max_base_dist * 1.15

    # ---------------------------------------------------------
    # 3. SOLUCIÓN HÍBRIDA (El Firewall)
    # ---------------------------------------------------------
    # Límite Inferior (Piso): Evita que el sistema sea hiper-estricto si los audios base fueron demasiado idénticos.
    # Límite Superior (Techo): Evita el Data Poisoning si el micrófono captura mucho ruido.
    MIN_SAFE = 0.10
    MAX_SAFE = 0.18

    # Aplica el piso y el techo en una sola línea elegante
    safe_th = min(max(organic_safe, MIN_SAFE), MAX_SAFE)
    safe_th = round(safe_th, 4)

    # 4. El Tribunal / Red de Pesca (Hard Negatives)
    # También lo limitamos superiormente a 0.27 para que no pesque ladridos de perro.
    organic_suspect = safe_th * 1.5
    suspect_th = min(round(organic_suspect, 4), 0.27)

    # --- LOGGING ---
    logger.info("-" * 50)
    logger.info("CALIBRACIÓN HÍBRIDA COMPLETADA")
    logger.info("Optimal Tightness calculado: %.2f", optimal_tightness)
    logger.info("Audios en grupo principal: %d/%d", len(grupo_principal), len(embeddings))
    logger.info("Varianza Orgánica (max_base_dist): %.4f", max_base_dist)
    logger.info("Safe Threshold: %.4f (Límites: %s - %s)", safe_th, MIN_SAFE, MAX_SAFE)
    logger.info("Suspect Threshold: %.4f (Techo: 0.27)", suspect_th)
    logger.info("-" * 50)

    # Debug visual
    logger.debug("Archivos en el grupo ganador:")
    for file in grupo_principal:
        logger.debug("   -> %s", file)

    return safe_th, suspect_th

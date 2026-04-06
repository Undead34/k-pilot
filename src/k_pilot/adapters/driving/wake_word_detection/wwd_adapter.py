"""Wake Word Detection (WWD) engine with adaptive learning capabilities.

Este módulo implementa un sistema de detección de palabras de activación
usando VAD (Voice Activity Detection) y DTW (Dynamic Time Warping) con
capacidad de aprendizaje activo.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import sounddevice as sd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from silero_vad import VADIterator

from k_pilot.core.shared.logging import get_logger
from k_pilot.core.shared.paths import paths

from . import wwd_db as db
from .wwd_audio_engine import (
    calculate_dynamic_thresholds,
    compare_audio_to_references,
    compute_normalized_dtw,
    get_vad_model,
    load_golden_embeddings,
    save_audio,
    trim_silence,
)

if TYPE_CHECKING:
    from typing import Any

    from numpy.typing import NDArray

    # Forward references para tipado estricto
    VadModel = Any


class DetectionResult(Enum):
    """Resultados posibles de la detección de wake word."""

    ACTIVATED = auto()
    SUSPICIOUS = auto()
    REJECTED = auto()
    DURATION_INVALID = auto()


@dataclass(frozen=True)
class AudioConfig:
    """Configuración de parámetros de audio."""

    sample_rate: int = 16_000
    window_size: int = 512
    min_silence_ms: int = 400
    speech_pad_ms: int = 30
    min_duration: float = 0.5
    max_duration: float = 1.8


@dataclass(frozen=True)
class Thresholds:
    """Umbrales de decisión para el clasificador."""

    safe: float = 0.12  # Match positivo rotundo
    suspect: float = 0.18  # Posible falso positivo (ruido/TV)


@dataclass(frozen=True)
class Directories:
    """Estructura de directorios para almacenamiento."""

    base: Path = paths.data_dir / "assets/base_samples/"
    core: Path = paths.data_dir / "assets/core_learned/"
    hard_negatives: Path = paths.data_dir / "assets/hard_negatives/"

    def ensure_exist(self) -> None:
        """Crea los directorios si no existen."""
        for directory in (self.base, self.core, self.hard_negatives):
            directory.mkdir(parents=True, exist_ok=True)


class WWDEngine:
    """Motor de detección de wake word con aprendizaje adaptativo.

    Esta clase gestiona la captura de audio, detección de voz,
    comparación con muestras base y almacenamiento de aprendizaje.

    Attributes:
        config: Parámetros de configuración de audio.
        thresholds: Umbrales de decisión para clasificación.
        dirs: Rutas de almacenamiento de muestras.
        logger: Instancia de logging para la clase.
    """

    def __init__(
        self,
        config: AudioConfig | None = None,
        thresholds: Thresholds | None = None,
        dirs: Directories | None = None,
    ) -> None:
        self.config = config or AudioConfig()
        self.thresholds = thresholds or Thresholds()
        self.dirs = dirs or Directories()
        self.logger = get_logger(self.__class__.__name__)

        self._vad_model: VadModel | None = None
        self._golden_records: dict[str, NDArray[np.float32]] | None = None

    def _initialize(self) -> None:
        """Inicializa recursos pesados (modelos VAD y base de datos)."""
        self.dirs.ensure_exist()
        db.init_db()
        self._vad_model = get_vad_model()
        self._golden_records = self._load_golden_records()
        new_safe, new_suspect = calculate_dynamic_thresholds(self._golden_records, self.logger)
        print(self.thresholds)
        self.thresholds = Thresholds(safe=new_safe, suspect=new_suspect)
        print(self.thresholds)

    def _load_golden_records(self) -> dict[str, NDArray[np.float32]]:
        """Carga las muestras base para comparación DTW.

        Returns:
            Diccionario de muestras de audio indexadas por nombre.

        Raises:
            RuntimeError: Si no se encuentran muestras base.
        """
        records = load_golden_embeddings(self.dirs.base)
        if not records:
            msg = (
                f"No base samples found in '{self.dirs.base}'. "
                "Add .wav files to this directory before starting."
            )
            self.logger.error(msg)
            raise RuntimeError(msg)
        return records

    def _process_audio_segment(self, audio_buffer: list[NDArray[np.float32]]) -> DetectionResult:
        """Procesa un segmento de audio completo (post-VAD).

        Args:
            audio_buffer: Lista de chunks de audio numpy.

        Returns:
            Resultado de la detección clasificado.
        """
        raw_audio = np.concatenate(audio_buffer)
        clean_audio = trim_silence(raw_audio, self.config.sample_rate)
        duration = len(clean_audio) / self.config.sample_rate

        # Validación de duración
        if not (self.config.min_duration <= duration <= self.config.max_duration):
            self.logger.debug("Audio rejected: duration %.2fs out of bounds", duration)
            return DetectionResult.DURATION_INVALID

        if not self._golden_records:
            raise Exception(f"_golden_records is none: +{self._golden_records}")

        # Comparación DTW
        best_match, distance = compare_audio_to_references(
            clean_audio, self._golden_records, self.config.sample_rate
        )

        if not best_match:
            raise Exception(f"_golden_records is none: {best_match}")

        timestamp = time.strftime("%X")
        audio_id = f"rec_{int(time.time())}_{uuid.uuid4().hex[:4]}.wav"

        return self._classify_and_store(timestamp, audio_id, clean_audio, best_match, distance)

    def _classify_and_store(
        self,
        timestamp: str,
        audio_id: str,
        audio: NDArray[np.float32],
        best_match: str,
        distance: float,
    ) -> DetectionResult:
        """Clasifica el audio y lo almacena según el resultado.

        Args:
            timestamp: Marca temporal para logging.
            audio_id: Identificador único del audio.
            audio: Array numpy con el audio limpio.
            best_match: Nombre de la muestra más similar.
            distance: Distancia DTW calculada.

        Returns:
            Categoría de detección asignada.
        """
        if distance < self.thresholds.safe:
            self.logger.info(
                "[%s] K-PILOT ACTIVATED! (dist=%.4f, match=%s)", timestamp, distance, best_match
            )
            self._save_learning_sample(audio_id, audio, "core", distance)
            return DetectionResult.ACTIVATED

        if distance < self.thresholds.suspect:
            self.logger.warning("[%s] Suspicious audio detected (dist=%.4f)", timestamp, distance)
            self._save_learning_sample(audio_id, audio, "hard_negative", distance)
            return DetectionResult.SUSPICIOUS

        self.logger.debug("[%s] Rejected (dist=%.4f)", timestamp, distance)
        return DetectionResult.REJECTED

    def _save_learning_sample(
        self,
        audio_id: str,
        audio: NDArray[np.float32],
        category: str,
        distance: float,
    ) -> None:
        """Persiste la muestra para entrenamiento futuro.

        Args:
            audio_id: Nombre del archivo a guardar.
            audio: Datos de audio.
            category: 'core' o 'hard_negative'.
            distance: Métrica de distancia para metadata.
        """
        target_dir = self.dirs.core if category == "core" else self.dirs.hard_negatives
        filepath = target_dir / audio_id

        try:
            save_audio(audio, str(filepath), self.config.sample_rate)
            db.register_audio(audio_id, category, distance)
        except (OSError, IOError) as e:
            self.logger.error("Failed to save audio %s: %s", audio_id, e)

    def _create_vad_iterator(self) -> VADIterator:
        """Factory para el iterador VAD configurado."""
        if self._vad_model is None:
            raise RuntimeError("VAD model not initialized. Call _initialize() first.")

        return VADIterator(
            self._vad_model,
            sampling_rate=self.config.sample_rate,
            min_silence_duration_ms=self.config.min_silence_ms,
            speech_pad_ms=self.config.speech_pad_ms,
        )

    def _handle_audio_chunk(
        self,
        chunk: NDArray[np.float32],
        vad_iterator: VADIterator,
        audio_buffer: list[NDArray[np.float32]],
        is_recording: bool,
    ) -> tuple[list[NDArray[np.float32]], bool]:
        """Procesa un chunk individual de audio del stream.

        Returns:
            Tupla de (buffer_actualizado, estado_de_grabación).
        """
        chunk_1d = chunk[:, 0]
        speech_dict = vad_iterator(chunk_1d)

        if not speech_dict:
            if is_recording:
                audio_buffer.append(chunk_1d)
            return audio_buffer, is_recording

        if "start" in speech_dict:
            return [chunk_1d], True

        if "end" in speech_dict:
            audio_buffer.append(chunk_1d)
            self._process_audio_segment(audio_buffer)
            return [], False

        return audio_buffer, is_recording

    def run(self) -> None:
        """Inicia el loop principal de detección.

        Este método bloquea hasta que se reciba KeyboardInterrupt
        o ocurra un error crítico.
        """
        self._initialize()

        self.logger.info("=" * 50)
        self.logger.info("K-PILOT ADAPTIVE ENGINE ONLINE")
        self.logger.info("Listening for wake word...")
        self.logger.info("=" * 50)

        audio_buffer: list[NDArray[np.float32]] = []
        is_recording = False
        vad_iterator = self._create_vad_iterator()

        try:
            with sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=1,
                dtype=np.float32,
                blocksize=self.config.window_size,
            ) as stream:
                while True:
                    chunk, overflowed = stream.read(self.config.window_size)

                    if overflowed:
                        self.logger.warning("Audio buffer overflow detected")

                    chunk_float32 = np.asarray(chunk, dtype=np.float32)

                    audio_buffer, is_recording = self._handle_audio_chunk(
                        chunk_float32, vad_iterator, audio_buffer, is_recording
                    )

        except KeyboardInterrupt:
            self.logger.info("Engine shut down by user")
        except Exception as e:
            self.logger.exception("Critical error in main loop: %s", e)
            raise

import logging
import os
from collections import defaultdict

import numpy as np
from lwake.features import dtw_cosine_normalized_distance, extract_embedding_features
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

logging.getLogger("local-wake").setLevel(logging.WARNING)


def agrupar_y_analizar_dataset(carpeta, umbral_distancia=0.10):
    archivos = sorted([f for f in os.listdir(carpeta) if f.endswith(".wav")])
    if not archivos:
        return

    print("Extrayendo características...")
    cache_features = {}
    for archivo in archivos:
        try:
            cache_features[archivo] = extract_embedding_features(
                path=os.path.join(carpeta, archivo)
            )
        except:
            pass

    archivos_procesados = list(cache_features.keys())
    n = len(archivos_procesados)

    print("Calculando matriz de distancias exacta...\n")
    matriz_distancias = np.zeros((n, n))

    for i in range(n):
        for j in range(i + 1, n):
            f1, f2 = archivos_procesados[i], archivos_procesados[j]
            dist = dtw_cosine_normalized_distance(cache_features[f1], cache_features[f2])
            matriz_distancias[i, j] = dist
            matriz_distancias[j, i] = dist

    distancias_condensadas = squareform(matriz_distancias)
    Z = linkage(distancias_condensadas, method="average")
    etiquetas = fcluster(Z, t=umbral_distancia, criterion="distance")

    grupos = defaultdict(list)
    for archivo, etiqueta_grupo in zip(archivos_procesados, etiquetas):
        grupos[etiqueta_grupo].append(archivo)

    grupos_ordenados = sorted(grupos.items(), key=lambda x: len(x[1]), reverse=True)

    print("=" * 70)
    print(
        f"{'Grupo':<6} | {'Audios':<6} | {'Intra-Dist':<10} | {'Frames Prom.':<12} | {'Medoide (Mejor Audio)':<20}"
    )
    print("-" * 70)

    for id_grupo, audios in grupos_ordenados:
        # Calcular Frames Promedio
        frames = [cache_features[f].shape[0] for f in audios]
        frames_promedio = np.mean(frames)

        # Calcular Intra-Distancia y encontrar el Medoide
        if len(audios) > 1:
            distancias_internas = []
            medias_por_archivo = {}
            for f1 in audios:
                dist_f1 = []
                for f2 in audios:
                    if f1 != f2:
                        dist = dtw_cosine_normalized_distance(
                            cache_features[f1], cache_features[f2]
                        )
                        distancias_internas.append(dist)
                        dist_f1.append(dist)
                        medias_por_archivo[f1] = np.mean(dist_f1)

            intra_dist_media = np.mean(distancias_internas)
            medoide = min(medias_por_archivo, key=medias_por_archivo.get)
        else:
            intra_dist_media = 0.0000
            medoide = audios[0]

        print(
            f"G-{id_grupo:<4} | {len(audios):<6} | {intra_dist_media:<10.4f} | {frames_promedio:<12.1f} | {medoide:<20}"
        )

    print("=" * 70)

    # Imprimir detalle de los grupos para saber qué archivos los componen
    for id_grupo, audios in grupos_ordenados:
        print(f"\nGrupo {id_grupo} ({len(audios)} audios):")
        for i in range(0, len(audios), 5):
            print("  " + ", ".join(audios[i : i + 5]))


def main():
    CARPETA_ASSETS = "/home/undead34/Projects/k-pilot/assets/hey k-pilot/"
    agrupar_y_analizar_dataset(CARPETA_ASSETS, umbral_distancia=0.1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Create or validate a normalized Planet Terrain Package.

Dependencies:
    Python 3.10+
    NumPy

For a new world, first convert its source DEM into three arrays:
    elevation_m      float32 [height, width]
    latitude_deg     float64 [height], strictly north-to-south
    longitude_deg    float64 [width], strictly west-to-east

Then call write_package().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import hashlib
import json
import numpy as np


SCHEMA = "planet-terrain-package"
SCHEMA_VERSION = "1.0.0"
DEFAULT_TRAVELER_SPECIFIC_HEAT = 2500.0


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def validate_arrays(
    elevation_m: np.ndarray,
    latitude_deg: np.ndarray,
    longitude_deg: np.ndarray,
) -> None:
    if elevation_m.dtype != np.float32:
        raise TypeError("elevation_m must be float32.")
    if latitude_deg.dtype != np.float64:
        raise TypeError("latitude_deg must be float64.")
    if longitude_deg.dtype != np.float64:
        raise TypeError("longitude_deg must be float64.")
    if elevation_m.ndim != 2:
        raise ValueError("elevation_m must be two-dimensional.")
    if latitude_deg.ndim != 1 or longitude_deg.ndim != 1:
        raise ValueError("Coordinate arrays must be one-dimensional.")
    if elevation_m.shape != (latitude_deg.size, longitude_deg.size):
        raise ValueError(
            "elevation_m.shape must equal "
            "(latitude_deg.size, longitude_deg.size)."
        )
    if not np.all(np.isfinite(latitude_deg)):
        raise ValueError("latitude_deg contains non-finite values.")
    if not np.all(np.isfinite(longitude_deg)):
        raise ValueError("longitude_deg contains non-finite values.")
    if not np.all(np.diff(latitude_deg) < 0):
        raise ValueError("latitude_deg must be strictly north-to-south.")
    if not np.all(np.diff(longitude_deg) > 0):
        raise ValueError("longitude_deg must be strictly west-to-east.")
    if latitude_deg[0] > 90.0 or latitude_deg[-1] < -90.0:
        raise ValueError("Latitude coordinates exceed the valid range.")
    if longitude_deg[0] < -180.0 or longitude_deg[-1] >= 180.0:
        raise ValueError("Use signed east-positive longitudes in [-180, 180).")


def write_package(
    output_directory: Path,
    *,
    planet: str,
    elevation_m: np.ndarray,
    latitude_deg: np.ndarray,
    longitude_deg: np.ndarray,
    radius_m: float,
    mass_kg: float,
    rotation_period_s: float,
    vertical_datum: str,
    source_data_path: Path | None = None,
    nodata_value: float | None = None,
    longitude_wraps: bool = True,
    traveler_specific_heat_j_kg_k: float = DEFAULT_TRAVELER_SPECIFIC_HEAT,
    provenance_notes: list[str] | None = None,
) -> None:
    elevation_m = np.asarray(elevation_m, dtype=np.float32)
    latitude_deg = np.asarray(latitude_deg, dtype=np.float64)
    longitude_deg = np.asarray(longitude_deg, dtype=np.float64)

    validate_arrays(elevation_m, latitude_deg, longitude_deg)

    for name, value in {
        "radius_m": radius_m,
        "mass_kg": mass_kg,
        "rotation_period_s": rotation_period_s,
        "traveler_specific_heat_j_kg_k": traveler_specific_heat_j_kg_k,
    }.items():
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive.")

    output_directory.mkdir(parents=True, exist_ok=True)
    terrain_path = output_directory / "terrain.npz"

    np.savez_compressed(
        terrain_path,
        elevation_m=elevation_m,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
    )

    valid = np.isfinite(elevation_m)
    if nodata_value is not None:
        valid &= elevation_m != np.float32(nodata_value)
    values = elevation_m[valid]

    metadata: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "planet": planet,
        "dataset_role": "normalized authoritative terrain source",
        "projection": {
            "name": "equirectangular",
            "coordinate_system":
                "planetocentric latitude and east-positive longitude",
            "longitude_units": "degrees",
            "latitude_units": "degrees",
        },
        "raster": {
            "height": int(elevation_m.shape[0]),
            "width": int(elevation_m.shape[1]),
            "layout": "elevation_m[y_index, x_index]",
            "row_order": "north_to_south",
            "column_order": "west_to_east",
            "longitude_wraps": bool(longitude_wraps),
            "latitude_sampling": "explicit_coordinate_vector",
            "longitude_sampling": "explicit_coordinate_vector",
            "elevation_dtype": "float32",
            "coordinate_dtype": "float64",
            "nodata_value": nodata_value,
        },
        "coordinates": {
            "latitude_first_deg": float(latitude_deg[0]),
            "latitude_last_deg": float(latitude_deg[-1]),
            "longitude_first_deg": float(longitude_deg[0]),
            "longitude_last_deg": float(longitude_deg[-1]),
        },
        "elevation": {
            "units": "m",
            "vertical_datum": vertical_datum,
            "minimum_valid_m": float(values.min()),
            "maximum_valid_m": float(values.max()),
            "mean_valid_m": float(values.mean(dtype=np.float64)),
            "valid_cell_count": int(valid.sum()),
            "total_cell_count": int(elevation_m.size),
        },
        "planetary_physics": {
            "radius_m": float(radius_m),
            "mass_kg": float(mass_kg),
            "rotation_period_s": float(rotation_period_s),
            "traveler_specific_heat_j_kg_k":
                float(traveler_specific_heat_j_kg_k),
        },
        "files": {
            "terrain_npz": "terrain.npz",
            "npz_arrays": {
                "elevation_m": {
                    "dtype": "float32",
                    "shape": list(elevation_m.shape),
                },
                "latitude_deg": {
                    "dtype": "float64",
                    "shape": [int(latitude_deg.size)],
                },
                "longitude_deg": {
                    "dtype": "float64",
                    "shape": [int(longitude_deg.size)],
                },
            },
        },
        "provenance": {
            "source_data_filename":
                source_data_path.name if source_data_path else None,
            "source_data_sha256":
                sha256_file(source_data_path) if source_data_path else None,
            "normalization_notes": provenance_notes or [],
        },
    }

    (output_directory / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )

    validate_package(output_directory)


def validate_package(package_directory: Path) -> None:
    metadata_path = package_directory / "metadata.json"
    terrain_path = package_directory / "terrain.npz"

    if not metadata_path.is_file() or not terrain_path.is_file():
        raise FileNotFoundError(
            "Package must contain metadata.json and terrain.npz."
        )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schema") != SCHEMA:
        raise ValueError("Unsupported or missing package schema.")

    with np.load(terrain_path, allow_pickle=False) as archive:
        required = {"elevation_m", "latitude_deg", "longitude_deg"}
        missing = required.difference(archive.files)
        if missing:
            raise ValueError(f"Missing NPZ arrays: {sorted(missing)}")

        elevation = archive["elevation_m"]
        latitude = archive["latitude_deg"]
        longitude = archive["longitude_deg"]
        validate_arrays(elevation, latitude, longitude)

        declared = metadata["raster"]
        if elevation.shape != (declared["height"], declared["width"]):
            raise ValueError("Metadata raster dimensions do not match NPZ.")

    print(f"Valid package: {package_directory}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "package",
        type=Path,
        help="Directory containing terrain.npz and metadata.json",
    )
    args = parser.parse_args()
    validate_package(args.package)


if __name__ == "__main__":
    main()

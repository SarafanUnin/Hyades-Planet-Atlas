from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


GRAVITATIONAL_CONSTANT = 6.67430e-11  # m^3 kg^-1 s^-2

# Used only when the Earth metadata does not provide the values.
EARTH_DEFAULTS = {
    "mass_kg": 5.9722e24,
    "mean_radius_m": 6_371_000.0,
    "sidereal_rotation_period_s": 86_164.0905,
}


def first_present(
    metadata: dict[str, Any],
    names: tuple[str, ...],
    default: float,
) -> float:
    """
    Return the first usable numeric metadata value among several possible keys.
    """

    for name in names:
        if name in metadata:
            value = metadata[name]

            if isinstance(value, (int, float)):
                return float(value)

    return float(default)


def load_planet_constants(
    metadata: dict[str, Any],
) -> tuple[float, float, float]:
    """
    Read mass, radius, and rotation period.

    The aliases make the script tolerant of several reasonable metadata
    naming conventions.
    """

    mass_kg = first_present(
        metadata,
        (
            "mass_kg",
            "planet_mass_kg",
            "mass",
        ),
        EARTH_DEFAULTS["mass_kg"],
    )

    radius_m = first_present(
        metadata,
        (
            "mean_radius_m",
            "radius_m",
            "planet_radius_m",
            "equatorial_radius_m",
        ),
        EARTH_DEFAULTS["mean_radius_m"],
    )

    rotation_period_s = first_present(
        metadata,
        (
            "sidereal_rotation_period_s",
            "rotation_period_s",
            "sidereal_day_s",
            "day_length_s",
        ),
        EARTH_DEFAULTS["sidereal_rotation_period_s"],
    )

    if mass_kg <= 0:
        raise ValueError("Planetary mass must be positive.")

    if radius_m <= 0:
        raise ValueError("Planetary radius must be positive.")

    if rotation_period_s <= 0:
        raise ValueError("Rotation period must be positive.")

    return mass_kg, radius_m, rotation_period_s


def latitude_grid(
    latitude_deg: np.ndarray,
    elevation_shape: tuple[int, int],
) -> np.ndarray:
    """
    Convert either a 1D latitude axis or a 2D latitude grid into a 2D grid.
    """

    latitude_deg = np.asarray(latitude_deg)

    if latitude_deg.ndim == 1:
        if latitude_deg.size != elevation_shape[0]:
            raise ValueError(
                "The 1D latitude array length does not match "
                "the elevation row count."
            )

        return np.broadcast_to(
            latitude_deg[:, np.newaxis],
            elevation_shape,
        )

    if latitude_deg.ndim == 2:
        if latitude_deg.shape != elevation_shape:
            raise ValueError(
                "The 2D latitude grid does not match the elevation shape."
            )

        return latitude_deg

    raise ValueError(
        "latitude_deg must be either a 1D axis or a 2D grid."
    )


def build_specific_energy(
    elevation_m: np.ndarray,
    latitude_deg: np.ndarray,
    mass_kg: float,
    radius_m: float,
    rotation_period_s: float,
) -> np.ndarray:
    """
    Calculate specific mechanical energy at every terrain cell.

    Result units:
        joules per kilogram
    """

    elevation = np.asarray(
        elevation_m,
        dtype=np.float64,
    )

    if elevation.ndim != 2:
        raise ValueError("elevation_m must be a 2D raster.")

    latitude = latitude_grid(
        latitude_deg,
        elevation.shape,
    ).astype(np.float64)

    surface_radius_m = radius_m + elevation

    if np.any(surface_radius_m <= 0):
        raise ValueError(
            "Terrain elevation produced a non-positive surface radius."
        )

    latitude_rad = np.deg2rad(latitude)

    gravitational_parameter = (
        GRAVITATIONAL_CONSTANT *
        mass_kg
    )

    angular_velocity_rad_s = (
        2.0 *
        math.pi /
        rotation_period_s
    )

    # Gravitational potential energy per kilogram.
    gravitational_j_per_kg = (
        -gravitational_parameter /
        surface_radius_m
    )

    # Distance from the planetary rotation axis.
    cylindrical_radius_m = (
        surface_radius_m *
        np.cos(latitude_rad)
    )

    rotational_speed_m_s = (
        angular_velocity_rad_s *
        cylindrical_radius_m
    )

    rotational_j_per_kg = (
        0.5 *
        rotational_speed_m_s**2
    )

    return (
        gravitational_j_per_kg +
        rotational_j_per_kg
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a float32 specific-mechanical-energy raster "
            "from terrain.npz and metadata.json."
        )
    )

    parser.add_argument(
        "planet_directory",
        type=Path,
        help="Directory containing terrain.npz and metadata.json.",
    )

    args = parser.parse_args()

    planet_directory = args.planet_directory

    terrain_path = (
        planet_directory /
        "terrain.npz"
    )

    metadata_path = (
        planet_directory /
        "metadata.json"
    )

    output_path = (
        planet_directory /
        "total_energy_j_per_kg.f32"
    )

    output_metadata_path = (
        planet_directory /
        "total_energy_j_per_kg.json"
    )

    if not terrain_path.is_file():
        raise FileNotFoundError(terrain_path)

    if not metadata_path.is_file():
        raise FileNotFoundError(metadata_path)

    with metadata_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        metadata = json.load(file)

    with np.load(terrain_path) as terrain:
        required_arrays = {
            "elevation_m",
            "latitude_deg",
            "longitude_deg",
        }

        missing = (
            required_arrays -
            set(terrain.files)
        )

        if missing:
            raise KeyError(
                "terrain.npz is missing: "
                + ", ".join(sorted(missing))
            )

        elevation_m = np.asarray(
            terrain["elevation_m"],
            dtype=np.float64,
        )

        latitude_deg = np.asarray(
            terrain["latitude_deg"],
            dtype=np.float64,
        )

        longitude_deg = np.asarray(
            terrain["longitude_deg"],
            dtype=np.float64,
        )

    mass_kg, radius_m, rotation_period_s = (
        load_planet_constants(metadata)
    )

    total_energy = build_specific_energy(
        elevation_m=elevation_m,
        latitude_deg=latitude_deg,
        mass_kg=mass_kg,
        radius_m=radius_m,
        rotation_period_s=rotation_period_s,
    )

    if not np.all(np.isfinite(total_energy)):
        raise ValueError(
            "The calculated energy raster contains NaN or infinity."
        )

    # The absolute gravitational term is about -62 MJ/kg for Earth.
    # Subtracting one reference value before converting to float32 preserves
    # much more precision when the shader subtracts two nearby cells.
    #
    # The zero point is arbitrary: only energy differences matter.
    energy_offset_j_per_kg = float(
        total_energy[0, 0]
    )

    stored_energy = (
        total_energy -
        energy_offset_j_per_kg
    ).astype("<f4")

    # C-contiguous, row-major, little-endian float32 data.
    stored_energy = np.ascontiguousarray(
        stored_energy
    )

    stored_energy.tofile(output_path)

    height, width = stored_energy.shape

    output_metadata = {
        "file": output_path.name,
        "dtype": "float32",
        "endianness": "little",
        "layout": "row-major",
        "shape": [height, width],
        "width": width,
        "height": height,
        "units": "J/kg",
        "quantity": "specific mechanical energy",
        "components": [
            "gravitational potential",
            "surface rotational kinetic energy",
        ],
        "stored_value_definition": (
            "physical_energy_j_per_kg - energy_offset_j_per_kg"
        ),
        "energy_offset_j_per_kg": energy_offset_j_per_kg,
        "mass_kg": mass_kg,
        "mean_radius_m": radius_m,
        "sidereal_rotation_period_s": rotation_period_s,
        "minimum_stored_j_per_kg": float(
            stored_energy.min()
        ),
        "maximum_stored_j_per_kg": float(
            stored_energy.max()
        ),
        "minimum_physical_j_per_kg": float(
            total_energy.min()
        ),
        "maximum_physical_j_per_kg": float(
            total_energy.max()
        ),
        "latitude_array_shape": list(
            latitude_deg.shape
        ),
        "longitude_array_shape": list(
            longitude_deg.shape
        ),
        "elevation_array_shape": list(
            elevation_m.shape
        ),
        "delta_sign_convention": {
            "released_energy_j_per_kg": (
                "origin_stored_energy - destination_stored_energy"
            ),
            "positive_delta_t": "heating",
            "negative_delta_t": "cooling",
        },
    }

    with output_metadata_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            output_metadata,
            file,
            indent=2,
        )

        file.write("\n")

    expected_bytes = (
        width *
        height *
        np.dtype("<f4").itemsize
    )

    actual_bytes = output_path.stat().st_size

    if actual_bytes != expected_bytes:
        raise RuntimeError(
            f"Unexpected file size: {actual_bytes}; "
            f"expected {expected_bytes}."
        )

    print(f"Planet directory: {planet_directory}")
    print(f"Raster dimensions: {width} × {height}")
    print(f"Mass: {mass_kg:.9e} kg")
    print(f"Radius: {radius_m:,.3f} m")
    print(
        "Rotation period: "
        f"{rotation_period_s:,.6f} s"
    )
    print(
        "Stored energy range: "
        f"{stored_energy.min():,.3f} to "
        f"{stored_energy.max():,.3f} J/kg"
    )
    print(f"Wrote: {output_path}")
    print(f"Wrote: {output_metadata_path}")


if __name__ == "__main__":
    main()
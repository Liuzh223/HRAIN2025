#!/usr/bin/env python3
"""Generate the HRAIN2025 30 km–500 m variable-resolution Hong Kong mesh.

The design uses 500 m cells over Hong Kong and transitions through 1, 4, and
12 km to a 30 km global background mesh. The resulting MPAS graph is partitioned
for 112 MPI tasks by default.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path
from types import ModuleType

import geopandas as gpd
import numpy as np
import xarray
from mpas_tools.io import write_netcdf
from mpas_tools.mesh.conversion import convert
from mpas_tools.mesh.creation.jigsaw_to_netcdf import jigsaw_to_netcdf
from mpas_tools.ocean.inject_meshDensity import inject_spherical_meshDensity
from scipy.spatial import cKDTree
from shapely import contains_xy


# Compatibility for older JIGSAW environments that import asyncio.log.
try:
    import asyncio.log  # type: ignore[import-not-found]  # noqa: F401
except ImportError:
    mock_asyncio_log = types.ModuleType("asyncio.log")
    mock_asyncio_log.logger = logging.getLogger("asyncio")  # type: ignore[attr-defined]
    sys.modules["asyncio.log"] = mock_asyncio_log


DEG_TO_KM = 111.0
DEFAULT_SAMPLE_DEG = 0.5 / 30.0
DEFAULT_BUFFER_DEG = 0.2
DEFAULT_PARTITIONS = 112

RES_1KM = 1.0
RES_4KM = 4.0
RES_12KM = 12.0
RES_OUTER = 30.0

DIST_500M_EDGE = 8.0
DIST_1KM_EDGE = 30.0
DIST_4KM_FULL = 150.0
DIST_12KM_EDGE = 400.0
DIST_30KM_EDGE = 775.0


def log(message: str) -> None:
    """Print a timestamped progress message."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def load_jigsaw_util(path: Path) -> ModuleType:
    """Load the workflow-specific jigsaw_util module from an explicit path."""
    if not path.is_file():
        raise FileNotFoundError(f"jigsaw_util.py not found: {path}")
    spec = importlib.util.spec_from_file_location("jigsaw_util", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load jigsaw_util.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_lon_lat_grid(sample_deg: float) -> tuple[np.ndarray, ...]:
    """Create the global sampling grid for the mesh-density field."""
    if sample_deg <= 0.0:
        raise ValueError("sample_deg must be positive")
    nlat = int(180.0 / sample_deg) + 1
    nlon = int(360.0 / sample_deg) + 1
    lat = np.linspace(-90.0, 90.0, nlat)
    lon = np.linspace(-180.0, 180.0, nlon)
    lons, lats = np.meshgrid(lon, lat)
    log(f"Sampling grid: {nlat} latitude x {nlon} longitude points")
    return lon, lat, lons, lats


def compute_boundary_distance_field(
    geojson_path: Path,
    lons: np.ndarray,
    lats: np.ndarray,
    buffer_deg: float,
) -> np.ndarray:
    """Compute distance in kilometres from the buffered Hong Kong boundary."""
    if not geojson_path.is_file():
        raise FileNotFoundError(f"Hong Kong GeoJSON not found: {geojson_path}")
    if buffer_deg < 0.0:
        raise ValueError("buffer_deg must be non-negative")

    log(f"Reading Hong Kong geometry from {geojson_path}")
    geometry = gpd.read_file(geojson_path)
    if geometry.empty:
        raise ValueError(f"No geometry found in {geojson_path}")
    if geometry.crs is not None and geometry.crs.to_epsg() != 4326:
        geometry = geometry.to_crs(epsg=4326)

    region = geometry.union_all().buffer(buffer_deg).convex_hull
    if region.is_empty or not hasattr(region, "exterior"):
        raise ValueError("Hong Kong geometry did not produce a valid polygon")

    inside = contains_xy(region, lons, lats)
    boundary_tree = cKDTree(np.asarray(region.exterior.coords))
    sample_points = np.column_stack((lons.ravel(), lats.ravel()))
    distance_deg, _ = boundary_tree.query(sample_points, k=1)
    distance_km = distance_deg.reshape(lons.shape) * DEG_TO_KM
    distance_km[inside] = 0.0
    return distance_km


def build_cell_width(distance_km: np.ndarray) -> np.ndarray:
    """Build the reference-consistent 0.5–30 km cell-width field."""
    cell_width = np.full(distance_km.shape, RES_OUTER, dtype=float)

    mask_500m = distance_km <= DIST_500M_EDGE
    mask_500m_to_1km = (
        (distance_km > DIST_500M_EDGE) & (distance_km <= DIST_1KM_EDGE)
    )
    mask_1km_to_4km = (
        (distance_km > DIST_1KM_EDGE) & (distance_km <= DIST_4KM_FULL)
    )
    mask_4km_to_12km = (
        (distance_km > DIST_4KM_FULL) & (distance_km <= DIST_12KM_EDGE)
    )
    mask_12km_to_30km = (
        (distance_km > DIST_12KM_EDGE) & (distance_km <= DIST_30KM_EDGE)
    )

    cell_width[mask_500m] = 0.5
    cell_width[mask_500m_to_1km] = 0.5 + (RES_1KM - 0.5) * (
        (distance_km[mask_500m_to_1km] - DIST_500M_EDGE)
        / (DIST_1KM_EDGE - DIST_500M_EDGE)
    )
    cell_width[mask_1km_to_4km] = RES_1KM + (RES_4KM - RES_1KM) * (
        (distance_km[mask_1km_to_4km] - DIST_1KM_EDGE)
        / (DIST_4KM_FULL - DIST_1KM_EDGE)
    )
    cell_width[mask_4km_to_12km] = RES_4KM + (RES_12KM - RES_4KM) * (
        (distance_km[mask_4km_to_12km] - DIST_4KM_FULL)
        / (DIST_12KM_EDGE - DIST_4KM_FULL)
    )
    cell_width[mask_12km_to_30km] = RES_12KM + (RES_OUTER - RES_12KM) * (
        (distance_km[mask_12km_to_30km] - DIST_12KM_EDGE)
        / (DIST_30KM_EDGE - DIST_12KM_EDGE)
    )
    return cell_width


def run_gpmetis(graph_path: Path, partitions: int) -> None:
    """Create a contiguous METIS partition for the MPAS graph."""
    if partitions <= 0:
        raise ValueError("partitions must be positive")
    if shutil.which("gpmetis") is None:
        raise FileNotFoundError("gpmetis is not available on PATH")
    command = [
        "gpmetis",
        "-minconn",
        "-contig",
        "-niter=200",
        str(graph_path),
        str(partitions),
    ]
    log("Running: " + " ".join(command))
    subprocess.run(command, check=True)


def generate_mesh(args: argparse.Namespace) -> None:
    """Generate, convert, annotate, and partition the Hong Kong MPAS mesh."""
    geojson_path = args.geojson.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_base = output_dir / "hk_hull_500m"
    jigsaw_util = load_jigsaw_util(args.jigsaw_util.resolve())

    lon, lat, lons, lats = make_lon_lat_grid(args.sample_deg)
    distance_km = compute_boundary_distance_field(
        geojson_path, lons, lats, args.buffer_deg
    )
    cell_width = build_cell_width(distance_km)

    log("Generating the 30 km–500 m spherical JIGSAW mesh")
    mesh_file = jigsaw_util.jigsaw_gen_sph_grid(
        cell_width, lon, lat, basename=str(output_base)
    )
    triangles_path = output_base.with_name(output_base.name + "_triangles.nc")
    mpas_path = output_base.with_suffix(".mpas.nc")
    graph_path = output_base.with_name(output_base.name + "_graph.info")

    jigsaw_to_netcdf(
        msh_filename=mesh_file,
        output_name=str(triangles_path),
        on_sphere=True,
        sphere_radius=1.0,
    )
    triangles = xarray.open_dataset(triangles_path)
    try:
        mpas_mesh = convert(
            triangles, dir=str(output_dir), graphInfoFileName=str(graph_path)
        )
        write_netcdf(mpas_mesh, str(mpas_path))
    finally:
        triangles.close()
        if "mpas_mesh" in locals() and hasattr(mpas_mesh, "close"):
            mpas_mesh.close()

    inject_spherical_meshDensity(
        cell_width, lon, lat, mesh_filename=str(mpas_path)
    )
    if not args.skip_partition:
        run_gpmetis(graph_path, args.partitions)
    log(f"Mesh generation complete: {mpas_path}")


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--geojson", type=Path, required=True, help="Hong Kong boundary GeoJSON"
    )
    parser.add_argument(
        "--jigsaw-util",
        type=Path,
        required=True,
        help="Path to the workflow's jigsaw_util.py",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("exp_500m"),
        help="Output directory (default: ./exp_500m)",
    )
    parser.add_argument("--sample-deg", type=float, default=DEFAULT_SAMPLE_DEG)
    parser.add_argument("--buffer-deg", type=float, default=DEFAULT_BUFFER_DEG)
    parser.add_argument("--partitions", type=int, default=DEFAULT_PARTITIONS)
    parser.add_argument(
        "--skip-partition", action="store_true", help="Do not run gpmetis"
    )
    return parser.parse_args()


def main() -> None:
    """Run the command-line mesh workflow."""
    generate_mesh(parse_args())


if __name__ == "__main__":
    main()

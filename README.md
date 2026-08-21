# HRAIN2025: 30 km–500 m Hong Kong MPAS experiment

This directory archives the configuration, mesh-generation source, and 112-way
graph partition for the HRAIN2025 MPAS-Atmosphere experiment over Hong Kong.
The namelists retain the experiment settings while using the tutorial's portable
run-directory conventions.

## Contents

- `config/namelist.init_atmosphere`: MPAS-Init configuration.
- `config/namelist.atmosphere`: MPAS-Atmosphere configuration.
- `vertical_levels/urban_ZR_75.txt`: 56 interface heights for the custom
  urban-aware vertical grid.
- `mesh/generate_hk_500m_mesh.py`: mesh-generation script for a 30 km global
  mesh with 500 m refinement around Hong Kong.
- `mesh/hk_hull_500m_graph.info.part.112`: METIS partition assignment for 112
  MPI tasks.

## Experiment summary

- Initialization window: `2025-08-04_00:00:00` to `2025-08-05_00:00:00`
- Model initial time: `2025-08-04_00:00:00`
- Model run duration: `3_00:00:00`
- Nominal inner/outer mesh spacing: 0.5 km / 30 km
- Vertical levels: 55
- Dynamics time step: 6 s
- Urban physics: enabled
- Graph partitions: 112

## Explicit physics configuration

The model namelist does not depend on `config_physics_suite`. It explicitly
selects the schemes used by the experiment:

- WSM6 microphysics
- New Tiedtke convection
- Noah-MP land surface model
- YSU planetary boundary layer and revised Monin–Obukhov surface layer
- YSU orographic gravity-wave drag
- Cloud-fraction diagnosis and RRTMG longwave/shortwave radiation

The HKUST-MPAS-LIU `rainfall` branch option
`config_cu_disable_dx = 10000.0` disables the non-scale-aware New Tiedtke
convection scheme wherever the local mesh spacing is below 10 km.

## Tutorial run-directory layout

Relative namelist paths are evaluated from the directory where the MPAS
executable is launched. The configuration assumes this layout:

```text
<tutorial-root>/
├── mpas_static/
└── run/
    ├── namelist.init_atmosphere
    ├── namelist.atmosphere
    ├── vertical_levels/
    │   └── urban_ZR_75.txt
    ├── GFS:<valid-time>
    └── hk_hull_500m_graph.info.part.112
```

Copy the files under `config/`, the `vertical_levels/` directory, and the
included partition file into `run/` before launching MPAS. In this layout,
`../mpas_static` and `vertical_levels/urban_ZR_75.txt` resolve to the intended
tutorial resources. This vertical-level path follows the layout documented by
the [HKUST MPAS-Urban project](https://github.com/HKUST-MPAS/HKUST-MPAS#vertical-grid-considerations-for-urban-simulations).

### GFS initial-condition files

GFS initial-condition files are not stored in this repository. The six files
used by the archived experiment are approximately 818 MB each (about 4.9 GB in
total), whereas GitHub blocks files larger than 100 MiB in ordinary Git
repositories. Obtain the required GFS data from its original data provider or
share it through a suitable research-data or object-storage service. Git LFS
is technically possible, but its storage and bandwidth quotas should be
checked before using it for a multi-gigabyte dataset. Place the downloaded
files in the run directory using the names expected by MPAS, such as
`GFS:<valid-time>`.

## Decomposition file

Both MPAS-Init and MPAS-Atmosphere use the prefix
`hk_hull_500m_graph.info.part.`. When launched with 112 MPI tasks, both resolve
to the included `hk_hull_500m_graph.info.part.112` file.

## Mesh-generation requirements

Install JIGSAW, METIS (`gpmetis`), `mpas_tools`, and the Python packages imported
by `mesh/generate_hk_500m_mesh.py`.

### Download `jigsaw_util.py`

The helper is maintained by Pedro S. Peixoto in the public MPAS-BR repository:

- Source: <https://github.com/pedrospeixoto/MPAS-BR/blob/master/grids/utilities/jigsaw/jigsaw_util.py>

Download it into the local `mesh/` directory:

```bash
curl -L \
  https://raw.githubusercontent.com/pedrospeixoto/MPAS-BR/master/grids/utilities/jigsaw/jigsaw_util.py \
  -o mesh/jigsaw_util.py
```

### Obtain a Hong Kong GeoJSON

Download a Hong Kong administrative-boundary GeoJSON and save it as
`mesh/hongkong.geojson`. A recommended authoritative source is the Hong Kong
Government's District Boundary dataset:

- Dataset page: <https://data.gov.hk/en-data/dataset/hk-had-json1-hong-kong-administrative-boundaries/resource/855f034a-c330-435c-a911-1d63538a6d55>

The dataset may contain all 18 districts as separate features. No manual merge
is required because the mesh script unions all features before constructing the
refinement boundary. The GeoJSON should use longitude/latitude coordinates;
other declared coordinate reference systems are converted to EPSG:4326.

If an administrative-boundary file is unavailable, the following command
creates an approximate circular Hong Kong refinement region. The circle is
constructed in UTM zone 50N so that its 42 km radius is defined in metres:

```bash
python - <<'PY'
import geopandas as gpd
from shapely.geometry import Point

center = gpd.GeoSeries(
    [Point(114.15, 22.35)],
    crs="EPSG:4326",
).to_crs("EPSG:32650")
geometry = center.buffer(42_000).to_crs("EPSG:4326").iloc[0]
data = gpd.GeoDataFrame(
    {
        "name": ["Hong Kong approximate circular region"],
        "radius_km": [42],
    },
    geometry=[geometry],
    crs="EPSG:4326",
)
data.to_file("mesh/hongkong.geojson", driver="GeoJSON")
PY
```

This circle is only a functional fallback. It changes the shape of the
high-resolution region and will not reproduce the archived mesh or its METIS
partition exactly.

### Generate the mesh

From the repository root, run:

```bash
python mesh/generate_hk_500m_mesh.py \
  --geojson mesh/hongkong.geojson \
  --jigsaw-util mesh/jigsaw_util.py \
  --output-dir generated_mesh
```

The default partition output is `hk_hull_500m_graph.info.part.112`. The default
global sampling field is memory intensive and should be generated on a
high-memory compute node.

## Provenance

The configuration files originate from the HRAIN2025 GFS 30 km–500 m MPAS
experiment. The mesh assets originate from the
`RMUI_CASE_Mesh/reference_consistent_30km_experiment` workflow.

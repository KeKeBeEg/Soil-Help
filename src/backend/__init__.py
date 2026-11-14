"""Backend: request JSON -> data path (US or global) -> ingestion -> analysis -> views.

    python run.py request.json

  request  parse and check the request JSON
  region   US (CONUS) or global, UTM zone
  plan     analysis grids and fixed analysis period for a field
  run      orchestration (the entry point)
  views    t_range outputs clipped to the field: GeoTIFFs, graphs, field_summary.json
"""

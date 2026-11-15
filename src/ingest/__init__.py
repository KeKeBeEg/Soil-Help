"""Data ingestion: pulls every analysis input for a field into the caches, then assembles the
input GeoTIFFs the analysis core (src.dataanal) reads.

  ee_io        Earth Engine login and tiled pixel downloads
  cache        cache keys and write-once cache files
  shared       datasets both paths use: IMERG rain, Landsat NDVI, HydroBASINS (global cache)
  global_data  FABDEM, SoilGrids, depth to bedrock, WorldCover            (global cache)
  us_data      3DEP (~10 m and 1 m lidar), SSURGO (gNATSGO + Soil Data Access), Cropland Data
               Layer (US cache)
  terrain      US path: 1 m elevation over the field for the water simulation, gaps from ~10 m
  nasa_data    NASA SMAP, ECOSTRESS, EMIT, FIRMS, HLS, MERRA-2 (global cache)
  collect      one call that fills working_save/inputs for a plan
"""

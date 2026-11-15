"""Data ingestion: pulls every analysis input for a field into the caches, then assembles the
input GeoTIFFs the analysis core (src.dataanal) reads.

  ee_io        Earth Engine login and tiled pixel downloads
  cache        cache keys and write-once cache files
  shared       datasets both paths use: IMERG rain, Landsat NDVI, HydroBASINS (global cache)
  global_data  FABDEM, SoilGrids, depth to bedrock, WorldCover            (global cache)
  us_data      3DEP, SSURGO (gNATSGO + Soil Data Access), Cropland Data Layer (US cache)
  nasa_data    NASA SMAP, ECOSTRESS, EMIT, FIRMS, HLS, MERRA-2 (global cache)
  collect      one call that fills working_save/inputs for a plan
"""

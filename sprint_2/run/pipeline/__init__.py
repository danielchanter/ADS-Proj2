"""
Modules behind build_master_dataset.py, one per source family:

    common       paths, HTTP helpers, key normalisation, source-coverage audit
    geo          straight-line nearest distances and point-in-SA2 counts
    listings     the Domain listing table, SA2 join, rent-derived columns
    abs_sources  ABS / Victoria in Future SA2 snapshots and yearly series
    access       schools, train stations, tram and bus stops, OSM amenities
    sqm          SQM Research weekly postcode rent index
    crime        Crime Statistics Victoria suburb rates
    routing      OpenRouteService driving routes
    land         Vicmap Property land size of each listing's lot
"""

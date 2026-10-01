"""
Map where the rental listings are.

Input:
    vic_rentals_all.csv

Output:
    output/maps/property_locations.html   interactive map, one point per listing
    output/maps/property_locations.png    the same points on the suburb boundaries

Both colour each listing by its advertised weekly rent and draw the ABS ASGS
2021 suburb (SAL) boundaries, the granularity the analysis is done at. The
interactive map can also shade each suburb by its median rent, and filters the
listings by rent, bedrooms and property type. The script reads the raw Domain
listings, so it runs on its own, ahead of build_master_dataset.py.

Install:
    pip install pandas geopandas shapely requests pyogrio folium matplotlib

Usage:
    python sprint_2/run/map_properties.py

Notes:
  * The HTML embeds every listing's address and rent. The Domain data must not
    be redistributed, so the maps are written under data/, which is gitignored.
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
from branca.element import MacroElement
from jinja2 import Template
from matplotlib import colormaps, patheffects
from matplotlib.colors import to_hex
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

from pipeline.abs_sources import load_sa2, load_sal_boundaries
from pipeline.common import DOMAIN_CSV, OUTPUT_DIR
from pipeline.correspondence import add_suburb_code, load_correspondence
from pipeline.listings import join_sa2, load_listings

# ---------------------------------------------------------------------
# RENT BANDS
# ---------------------------------------------------------------------

# Upper bound (exclusive) and label. The breaks sit near the 15th, 45th, 70th
# and 85th percentiles of weekly_rent, rounded to $50. weekly_rent is
# uncleaned, and banding puts the handful of listings above $5,000 a week in
# the top band with the rest of the expensive ones.
RENT_BANDS = [
    (450, "Under $450"),
    (550, "$450 to $549"),
    (650, "$550 to $649"),
    (800, "$650 to $799"),
    (np.inf, "$800 or more"),
]
NO_RENT_LABEL = "No rent listed"
NO_RENT_COLOUR = "#898781"

# The bands are coloured from viridis, which is perceptually uniform: colours
# the same distance apart on it look equally different, so evenly spaced
# samples make each band as distinct from the next as any other pair. It runs
# from dark purple at 0 to yellow at 1, and the bands go from RENT_COLOUR_LIGHT
# down to 0, so a higher rent is darker. Above 0.7 the greens and yellows are
# too faint to see on LAND_COLOUR.
RENT_COLOUR_MAP = "viridis"
RENT_COLOUR_LIGHT = 0.7
BAND_COLOURS = {
    label: to_hex(colormaps[RENT_COLOUR_MAP](position))
    for (_, label), position in zip(RENT_BANDS, np.linspace(RENT_COLOUR_LIGHT, 0, len(RENT_BANDS)))
} | {NO_RENT_LABEL: NO_RENT_COLOUR}

LAND_COLOUR = "#f0efec"
BOUNDARY_COLOUR = "#c3c2b7"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"

# VicGrid GDA2020, the equal-area CRS land.py measures in. Used to draw the
# static map undistorted and to simplify the boundaries in metres.
MAP_CRS = "EPSG:7899"

# The suburb boundaries are 57 MB as GeoJSON, too heavy to embed in a web page.
# Simplified as one coverage, so neighbouring suburbs keep a shared edge, they
# come to about 5 MB.
WEB_SIMPLIFY_M = 100

# Esri's Light Gray Canvas, a base map made to sit behind data. It comes as two
# tile sets, the land and roads ("Base") and the names ("Reference"), and needs
# no API key. Its tiles stop at zoom 16; MAX_ZOOM lets the map go one level
# closer, where they are enlarged to twice their size and still read.
MAX_ZOOM = 17
# The Reference tiles are only used from this zoom, where they name the
# streets. Their place names are small, grey and blurred on a high-resolution
# screen, so the map writes the suburb names itself.
STREET_NAMES_MIN_ZOOM = 16
ESRI_GREY_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
    "World_Light_Gray_{part}/MapServer/tile/{{z}}/{{y}}/{{x}}"
)
ESRI_GREY_OPTIONS = {
    "attr": "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ",
    "max_native_zoom": 16,
    "max_zoom": MAX_ZOOM,
}

# The font of the legend panel and the suburb names.
UI_FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"

# A name is written where the largest circle that fits inside its suburb is
# centred, which is found to within this distance.
LABEL_POINT_TOLERANCE_M = 50

# A suburb is shaded by its median rent only when at least this many of its
# listings have a rent. The median of one or two listings says little about
# the suburb.
MIN_PRICED_FOR_MEDIAN = 5

NOT_LISTED = "Not listed"

# The interactive map's filters besides the rent bands. Bedrooms are grouped
# as 1 to 4 and 5+; the three property types below cover 97% of the listings
# and the rest are grouped as "Other".
BEDROOM_OPTIONS = ["1", "2", "3", "4", "5+", NOT_LISTED]
TYPE_GROUPS = {
    "House": "House",
    "Apartment / Unit / Flat": "Apartment",
    "Townhouse": "Townhouse",
}
OTHER_TYPE = "Other"
TYPE_OPTIONS = list(TYPE_GROUPS.values()) + [OTHER_TYPE]

# The static map's three panels: Victoria, Greater Melbourne, and a square this
# far each way from the centre of the suburb of Melbourne, where the listings
# are too dense to tell apart at the Greater Melbourne scale.
INNER_MELBOURNE_HALF_WIDTH_M = 12_000

# Suburbs named on each panel of the static map, to orient the reader.
VICTORIA_PLACES = [
    "Mildura", "Swan Hill", "Horsham", "Bendigo", "Shepparton", "Wodonga",
    "Ballarat Central", "Geelong", "Warrnambool", "Traralgon", "Bairnsdale",
]
# Greater Melbourne's are the outer ones: a name on the centre would cover it.
MELBOURNE_PLACES = [
    "Geelong", "Werribee", "Melton", "Sunbury", "Craigieburn", "Pakenham",
    "Frankston", "Mornington",
]
INNER_MELBOURNE_PLACES = [
    "Melbourne", "Footscray", "Essendon", "Brunswick", "Preston", "Kew",
    "Box Hill", "Richmond", "South Yarra", "St Kilda", "Caulfield", "Brighton",
]


def rent_band(weekly_rent):
    """The RENT_BANDS label for each rent; a missing or zero rent is NO_RENT_LABEL."""
    rent = weekly_rent.where(weekly_rent > 0)
    band = pd.cut(
        rent,
        bins=[0] + [upper for upper, _ in RENT_BANDS],
        labels=[label for _, label in RENT_BANDS],
        right=False,
    )
    return band.astype(object).fillna(NO_RENT_LABEL)

# ---------------------------------------------------------------------
# DATA
# ---------------------------------------------------------------------

def load_located_listings(path, sa2, cache):
    """
    Listings that have coordinates, as points, with their rent band, their
    suburb and the SA2 attributes that say whether they are in Greater Melbourne.
    """
    listings = join_sa2(load_listings(path), sa2)
    # The same (suburb, postcode) match the master table uses, so a suburb's
    # numbers on the map are the ones the analysis gets for it.
    listings = add_suburb_code(listings, load_correspondence(cache, coverage=[]), coverage=[])
    located = listings["lat"].notna() & listings["lon"].notna()
    if (~located).any():
        print(f"Left off the maps: {(~located).sum()} listings with no coordinates")
    listings = listings.loc[located].copy()
    listings["rent_band"] = rent_band(listings["weekly_rent"])
    return listings


def summarise_suburbs(suburbs, listings):
    """
    Suburb polygons with the number of listings in each, how many of those
    have a rent, and their median rent.
    """
    rent = listings["weekly_rent"].where(listings["weekly_rent"] > 0)
    summary = rent.groupby(listings["sal_code_2021"]).agg(
        listing_count="size", priced_count="count", median_weekly_rent="median"
    )
    out = suburbs.merge(summary, left_on="sal_code_2021", right_index=True, how="left")
    for col in ["listing_count", "priced_count"]:
        out[col] = out[col].fillna(0).astype(int)
    return out


def dollars(value):
    return NOT_LISTED if pd.isna(value) or value <= 0 else f"${value:,.0f}"


def describe_rooms(row):
    parts = [
        f"{row[col]:.0f} {unit}"
        for col, unit in [("bedrooms", "bed"), ("bathrooms", "bath"), ("carspaces", "car")]
        if pd.notna(row[col])
    ]
    return ", ".join(parts) or NOT_LISTED


def label_circles(geometry):
    """
    Where to write each suburb's name: the centre, in MAP_CRS, and the radius,
    in metres, of the largest circle that fits inside the suburb. That is the
    middle of its widest part. The centroid of an L-shaped suburb can be
    outside it, and representative_point() is inside but often near an edge.
    """
    circles = shapely.maximum_inscribed_circle(
        geometry.to_crs(MAP_CRS).values, tolerance=LABEL_POINT_TOLERANCE_M
    )
    # Each circle comes as the line from its centre to the nearest boundary.
    centres = gpd.GeoSeries(shapely.get_point(circles, 0), index=geometry.index, crs=MAP_CRS)
    return centres, shapely.length(circles)


def bedroom_group(bedrooms):
    """The BEDROOM_OPTIONS entry for each bedroom count."""
    group = bedrooms.clip(upper=5).map("{:.0f}".format).replace("5", "5+")
    return group.where(bedrooms.notna(), NOT_LISTED)

# ---------------------------------------------------------------------
# INTERACTIVE MAP
# ---------------------------------------------------------------------

def listing_layer(listings):
    """One circle per listing, coloured by rent band, with its details on hover."""
    shown = gpd.GeoDataFrame(
        {
            # Tooltips are rendered as HTML, and addresses contain "&".
            "address": listings["address"].fillna("Address not listed").map(html.escape),
            "suburb": listings["suburb"].str.title() + " " + listings["postcode"].astype(str),
            "rent": listings["weekly_rent"].map(dollars),
            "type": listings["property_type"].fillna(NOT_LISTED),
            "rooms": listings.apply(describe_rooms, axis=1),
            # The three values the filters test.
            "band": listings["rent_band"],
            "beds": bedroom_group(listings["bedrooms"]),
            "kind": listings["property_type"].map(TYPE_GROUPS).fillna(OTHER_TYPE),
        },
        geometry=listings.geometry,
        crs=listings.crs,
    )
    return folium.GeoJson(
        shown,
        name="Rental listings",
        # The radius and the ring are set by MapBehaviour, which scales them
        # with the zoom.
        marker=folium.CircleMarker(radius=3, weight=0, color=SURFACE, fill=True, fill_opacity=0.9),
        style_function=lambda feature: {"fillColor": BAND_COLOURS[feature["properties"]["band"]]},
        tooltip=folium.GeoJsonTooltip(
            fields=["address", "suburb", "rent", "type", "rooms"],
            aliases=["Address", "Suburb", "Weekly rent", "Type", "Rooms"],
        ),
    )


def web_suburbs(suburb_summary):
    """The suburbs as the web map shows them: display columns and light boundaries."""
    shown = suburb_summary[["sal_name_2021", "listing_count", "priced_count"]].copy()
    shown["median_weekly_rent"] = suburb_summary["median_weekly_rent"].map(dollars)
    shown["band"] = rent_band(suburb_summary["median_weekly_rent"])
    # ABS adds the state to a name other states share, as in "Richmond (Vic.)".
    names = suburb_summary["sal_name_2021"].str.replace(r" \(.*\)$", "", regex=True)
    # Names are written until they run out of room, the suburbs with the most
    # listings first. The cities and towns go ahead of those, or a town seen
    # from the state would be named after whichever of its suburbs is busiest.
    shown["label_first"] = names.isin(VICTORIA_PLACES + ["Melbourne"])
    # Only a city loses its " Central": Kinglake Central is not Kinglake.
    shown["label"] = names.where(~shown["label_first"], names.str.removesuffix(" Central"))
    centres, radii = label_circles(suburb_summary.geometry)
    centres = centres.to_crs("EPSG:4326")
    shown["label_lat"] = centres.y.round(5)
    shown["label_lon"] = centres.x.round(5)
    # How much room the suburb has for its name.
    shown["label_radius_m"] = radii.round().astype(int)
    simplified = suburb_summary.geometry.to_crs(MAP_CRS).simplify_coverage(WEB_SIMPLIFY_M)
    # 5 dp is about 1 m, well within the 100 m simplification.
    shown = shown.set_geometry(shapely.set_precision(simplified.to_crs("EPSG:4326").values, 1e-5))
    return shown.set_crs("EPSG:4326")


def suburb_tooltip():
    return folium.GeoJsonTooltip(
        fields=["sal_name_2021", "listing_count", "median_weekly_rent"],
        aliases=["Suburb", "Listings", "Median weekly rent"],
    )


def suburb_layer(suburbs):
    """Suburb outlines; hovering one gives its name, listing count and median rent."""
    return folium.GeoJson(
        suburbs.drop(columns=["priced_count", "band"]),
        name="Suburb boundaries",
        style_function=lambda feature: {"color": INK_MUTED, "weight": 0.6, "opacity": 0.6, "fillOpacity": 0},
        highlight_function=lambda feature: {"color": INK, "weight": 1.5, "opacity": 1, "fillOpacity": 0.06},
        tooltip=suburb_tooltip(),
    )


def suburb_median_layer(suburbs):
    """
    The suburbs with enough priced listings, filled with the rent band of their
    median rent. Off until it is ticked in the layer list: under the listings
    it would put the dots on suburbs of the same colours.
    """
    enough = suburbs[suburbs["priced_count"] >= MIN_PRICED_FOR_MEDIAN]
    return folium.GeoJson(
        enough.drop(columns=["priced_count"] + [col for col in enough.columns if col.startswith("label")]),
        name="Suburb median rent",
        show=False,
        style_function=lambda feature: {
            "fillColor": BAND_COLOURS[feature["properties"]["band"]],
            "fillOpacity": 0.7, "color": SURFACE, "weight": 1,
        },
        highlight_function=lambda feature: {"fillOpacity": 0.9, "color": INK, "weight": 1.5},
        tooltip=suburb_tooltip(),
    )


# An outline around each suburb name, so it reads over the listings: eight
# copies of the name in the background colour, 1.5px out in each direction,
# and a soft edge around those.
NAME_HALO = ", ".join(
    [f"{1.5 * np.cos(angle):.2f}px {1.5 * np.sin(angle):.2f}px 0 {SURFACE}"
     for angle in np.linspace(0, 2 * np.pi, 8, endpoint=False)]
    + [f"0 0 4px {SURFACE}"]
)

PANEL_CSS = f"""
<style>
.suburb-name span {{ position: absolute; transform: translate(-50%, -50%); white-space: pre;
                     text-align: center; color: {INK}; text-shadow: {NAME_HALO}; }}
.suburb-name .quiet {{ color: {INK_SECONDARY}; }}
.lp {{ position: fixed; bottom: 64px; left: 10px; z-index: 1000; width: 232px;
       max-height: calc(100vh - 90px); overflow-y: auto; box-sizing: border-box;
       padding: 12px 14px; background: {SURFACE}; color: {INK};
       border: 1px solid rgba(11,11,11,0.1); border-radius: 8px;
       font: 12px/1.45 {UI_FONT}; }}
.lp-title {{ font-size: 13px; font-weight: 600; }}
.lp-note {{ color: {INK_SECONDARY}; }}
.lp fieldset {{ margin: 10px 0 0; padding: 0; border: 0; }}
.lp legend {{ padding: 0; margin-bottom: 4px; color: {INK_SECONDARY}; font-size: 11px;
              font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; }}
.lp-band {{ display: flex; align-items: center; gap: 8px; margin: 0; padding: 2px 0;
            font-weight: 400; cursor: pointer; }}
.lp-band input {{ margin: 0; accent-color: {INK_SECONDARY}; }}
.lp-dot {{ width: 11px; height: 11px; border-radius: 50%; flex: none; }}
.lp-name {{ flex: 1; }}
.lp-count {{ color: {INK_SECONDARY}; font-variant-numeric: tabular-nums; }}
.lp-chips {{ display: flex; flex-wrap: wrap; gap: 4px; }}
.lp-chip {{ margin: 0; font-weight: 400; cursor: pointer; }}
.lp-chip input {{ position: absolute; opacity: 0; pointer-events: none; }}
.lp-chip span {{ display: block; padding: 2px 8px; border-radius: 999px;
                 border: 1px solid {BOUNDARY_COLOUR}; color: {INK_MUTED}; }}
.lp-chip input:checked + span {{ border-color: {INK_SECONDARY}; background: {INK_SECONDARY}; color: {SURFACE}; }}
.lp-chip input:focus-visible + span {{ outline: 2px solid {INK}; outline-offset: 1px; }}
.lp-band:has(input:not(:checked)) .lp-name, .lp-band:has(input:not(:checked)) .lp-count {{ color: {INK_MUTED}; }}
.lp-band:has(input:not(:checked)) .lp-dot {{ opacity: 0.3; }}
.lp-reset {{ margin-top: 10px; padding: 0; border: 0; background: none; cursor: pointer;
             color: {INK_SECONDARY}; font: inherit; text-decoration: underline; }}
.lp-foot {{ margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(11,11,11,0.1);
            color: {INK_SECONDARY}; font-size: 11px; }}
</style>
"""


def chips_html(key, options):
    """A row of toggle chips, one per value of the listing property `key`."""
    chips = "".join(
        f'<label class="lp-chip"><input type="checkbox" data-filter="{key}" '
        f'value="{html.escape(option)}" checked><span>{html.escape(option)}</span></label>'
        for option in options
    )
    return f'<div class="lp-chips">{chips}</div>'


def panel_html(listings):
    """The legend, which is also the filter: unticking a value hides its listings."""
    counts = listings["rent_band"].value_counts()
    bands = "".join(
        f'<label class="lp-band"><input type="checkbox" data-filter="band" '
        f'value="{html.escape(label)}" checked>'
        f'<span class="lp-dot" style="background:{colour}"></span>'
        f'<span class="lp-name">{label}</span>'
        f'<span class="lp-count">{counts.get(label, 0):,}</span></label>'
        for label, colour in BAND_COLOURS.items()
    )
    return (
        f'{PANEL_CSS}<div class="lp" id="listing-panel">'
        f'<div class="lp-title">Victorian rental listings</div>'
        f'<div class="lp-note">Showing <span id="listing-count">{len(listings):,}</span>'
        f' of {len(listings):,}</div>'
        f'<fieldset><legend>Weekly rent</legend>{bands}</fieldset>'
        f'<fieldset><legend>Bedrooms</legend>{chips_html("beds", BEDROOM_OPTIONS)}</fieldset>'
        f'<fieldset><legend>Property type</legend>{chips_html("kind", TYPE_OPTIONS)}</fieldset>'
        f'<button type="button" class="lp-reset" id="listing-reset">Show all listings</button>'
        f'<div class="lp-foot">Tick <b>Suburb median rent</b> in the layer list to shade each'
        f' suburb in the same colours. Only suburbs where {MIN_PRICED_FOR_MEDIAN} or more'
        f' listings have a rent are shaded.</div>'
        f'</div>'
    )


class MapBehaviour(MacroElement):
    """
    The JavaScript folium has no option for: the filters, a listing radius that
    grows with the zoom, keeping the layers in the right order, the suburb
    names, and showing the grey map's street names only with the grey map.
    """

    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
            var map = {{ this._parent.get_name() }};
            var listings = {{ this.listings.get_name() }};
            var outlines = {{ this.outlines.get_name() }};
            var grey = {{ this.grey.get_name() }};
            var streetNames = {{ this.street_names.get_name() }};
            var suburbNames = {{ this.suburb_names.get_name() }};
            var FONT = {{ this.font|tojson }};
            // Kept apart from the layer, which only holds the listings shown.
            var all = listings.getLayers();
            var panel = document.getElementById("listing-panel");
            var boxes = Array.from(panel.querySelectorAll("input[data-filter]"));

            // A listing is shown when every filter has its value ticked.
            function applyFilters() {
                var ticked = {};
                boxes.forEach(function (box) {
                    var key = box.dataset.filter;
                    ticked[key] = ticked[key] || {};
                    ticked[key][box.value] = box.checked;
                });
                var keys = Object.keys(ticked);
                var shown = 0;
                all.forEach(function (layer) {
                    var p = layer.feature.properties;
                    if (keys.every(function (key) { return ticked[key][p[key]]; })) {
                        listings.addLayer(layer);
                        shown += 1;
                    } else {
                        listings.removeLayer(layer);
                    }
                });
                document.getElementById("listing-count").textContent = shown.toLocaleString("en-AU");
            }
            panel.addEventListener("change", applyFilters);
            document.getElementById("listing-reset").addEventListener("click", function () {
                boxes.forEach(function (box) { box.checked = true; });
                applyFilters();
            });

            // Small dots for the state, where they overlap; larger, and ringed
            // to stay apart, at street level.
            function resize() {
                var zoom = map.getZoom();
                var style = {
                    radius: zoom < 11 ? 2 : Math.min(7, 2 + 0.8 * (zoom - 10)),
                    weight: zoom < 13 ? 0 : 1,
                };
                all.forEach(function (layer) { layer.setStyle(style); });
            }
            map.on("zoomend", resize);
            resize();

            // Seen from the state, 2,900 outlines at full weight are a grey mesh.
            function thin(layer) {
                var zoom = map.getZoom();
                layer.setStyle({weight: zoom < 9 ? 0.2 : zoom < 12 ? 0.6 : 1});
            }
            map.on("zoomend", function () { thin(outlines); });
            // After folium has put the suburb the pointer left back to its first weight.
            outlines.on("mouseout", function (e) { thin(e.layer); });
            thin(outlines);

            // The canvas draws, and hovers, in the order layers were added, so a
            // suburb layer ticked back on would cover the listings.
            map.on("overlayadd", function () {
                if (map.hasLayer(listings)) { listings.bringToFront(); }
            });

            // The street map has street names of its own.
            map.on("baselayerchange", function (e) {
                if (e.layer === grey) { streetNames.addTo(map); } else { streetNames.remove(); }
            });

            // A long name is written on two lines, broken at the space nearest
            // its middle, so it takes less room across the map.
            function wrap(name) {
                var middle = name.length / 2;
                var at = -1;
                for (var i = name.indexOf(" "); i >= 0; i = name.indexOf(" ", i + 1)) {
                    if (at < 0 || Math.abs(i - middle) < Math.abs(at - middle)) { at = i; }
                }
                return name.length < 12 || at < 0 ? [name] : [name.slice(0, at), name.slice(at + 1)];
            }

            // The suburbs to name, in the order they get the room: the cities
            // and towns, then the suburbs with the most listings, then the
            // largest of the rest.
            var suburbs = outlines.getLayers().map(function (layer) {
                var p = layer.feature.properties;
                return {lines: wrap(p.label), city: p.label_first, listings: p.listing_count,
                        radius: p.label_radius_m, at: L.latLng(p.label_lat, p.label_lon)};
            }).sort(function (a, b) {
                return b.city - a.city || b.listings - a.listings || b.radius - a.radius;
            });

            // The cities and towns are written largest. A suburb with no
            // listings is written smaller and, by its class, in grey, so the
            // places the data is about stand out.
            function nameFont(suburb, zoom) {
                if (suburb.city) { return {size: 14, weight: 700, cls: ""}; }
                if (suburb.listings) { return {size: zoom < 13 ? 11 : 12, weight: 600, cls: ""}; }
                return {size: 11, weight: 500, cls: "quiet"};
            }

            // Measures a name before it is written, to see whether it fits.
            var pen = document.createElement("canvas").getContext("2d");
            var LINE_HEIGHT = 1.15;

            // The space a control takes on the map, as a name's is recorded
            // below: its centre and half its width and height, in pixels.
            function controlBox(control) {
                var frame = map.getContainer().getBoundingClientRect();
                var r = control.getBoundingClientRect();
                return {x: r.left + r.width / 2 - frame.left, y: r.top + r.height / 2 - frame.top,
                        w: r.width / 2, h: r.height / 2};
            }

            // Names every suburb in view that has room, in that order: a name
            // is left out when it would run into one already written, or sit
            // under the legend or the layer list.
            function nameSuburbs() {
                suburbNames.clearLayers();
                var zoom = map.getZoom();
                // A little past the edge, so a name is already there when the
                // map is dragged to it.
                var view = map.getBounds().pad(0.1);
                // The clear space, in pixels, a name needs around it. More from
                // further out, where a few names well apart read better than
                // many side by side.
                var space = zoom < 9 ? 20 : zoom < 13 ? 10 : 4;
                var written = [panel, document.querySelector(".leaflet-control-layers")].map(controlBox);
                suburbs.forEach(function (suburb) {
                    // From further out, only the towns and the suburbs with
                    // listings are named.
                    var named = zoom >= 12 || suburb.city || suburb.listings;
                    if (!named || !view.contains(suburb.at)) { return; }
                    var font = nameFont(suburb, zoom);
                    var css = font.weight + " " + font.size + "px/" + LINE_HEIGHT + " " + FONT;
                    pen.font = css;
                    var width = Math.max.apply(null, suburb.lines.map(function (line) {
                        return pen.measureText(line).width;
                    }));
                    var height = suburb.lines.length * font.size * LINE_HEIGHT;
                    // The suburb's inscribed circle in pixels: Leaflet's tiles
                    // are 256 pixels across a 40,075 km equator at zoom 0.
                    var metresPerPixel = 40075017 * Math.cos(suburb.at.lat * Math.PI / 180)
                        / (256 * Math.pow(2, zoom));
                    var radius = suburb.radius / metresPerPixel;
                    // A suburb too small on screen to hold its name is a
                    // cluster of dots, and the name goes above it, not on it.
                    var lift = radius < height / 2 + 2 ? Math.round(radius + height / 2 + 3) : 0;
                    var centre = map.latLngToContainerPoint(suburb.at);
                    var box = {x: centre.x, y: centre.y - lift, w: width / 2, h: height / 2};
                    // The cities and towns only have to keep off each other.
                    var gap = suburb.city ? 2 : space;
                    var clash = written.some(function (other) {
                        return Math.abs(other.x - box.x) < other.w + box.w + gap
                            && Math.abs(other.y - box.y) < other.h + box.h + gap;
                    });
                    if (clash) { return; }
                    written.push(box);
                    var text = document.createElement("span");
                    text.textContent = suburb.lines.join("\\n");
                    text.className = font.cls;
                    text.style.font = css;
                    text.style.top = -lift + "px";
                    L.marker(suburb.at, {
                        icon: L.divIcon({className: "suburb-name", html: text, iconSize: [0, 0]}),
                        pane: "suburb-names", interactive: false, keyboard: false,
                    }).addTo(suburbNames);
                });
            }
            map.on("moveend", nameSuburbs);
            nameSuburbs();
        })();
        {% endmacro %}
    """)

    def __init__(self, listings, outlines, suburb_names, grey, street_names):
        super().__init__()
        self._name = "MapBehaviour"
        self.listings = listings
        self.outlines = outlines
        self.suburb_names = suburb_names
        self.grey = grey
        self.street_names = street_names
        self.font = UI_FONT


def build_interactive_map(listings, suburb_summary):
    # Canvas rendering draws all 12,000 circles on one element, which keeps
    # the map responsive.
    m = folium.Map(tiles=None, prefer_canvas=True, control_scale=True, max_zoom=MAX_ZOOM)

    # Grey to start with, so the only colour on the map is the rent. The
    # street map is the alternative, faded so the listings stand out on it.
    grey = folium.TileLayer(
        ESRI_GREY_URL.format(part="Base"), name="Grey map", **ESRI_GREY_OPTIONS
    ).add_to(m)
    folium.TileLayer("OpenStreetMap", name="Street map", opacity=0.6, show=False).add_to(m)
    street_names = folium.TileLayer(
        ESRI_GREY_URL.format(part="Reference"), min_zoom=STREET_NAMES_MIN_ZOOM,
        overlay=True, control=False, **ESRI_GREY_OPTIONS,
    ).add_to(m)

    suburbs = web_suburbs(suburb_summary)
    outlines = suburb_layer(suburbs).add_to(m)
    suburb_median_layer(suburbs).add_to(m)
    points = listing_layer(listings).add_to(m)
    # MapBehaviour writes the suburb names into this layer. Its pane is above
    # the listings and the suburb shading, which would otherwise cover the
    # names, and ignores the pointer, so hovering reaches what is underneath.
    folium.map.CustomPane("suburb-names", z_index=450, pointer_events=False).add_to(m)
    suburb_names = folium.FeatureGroup(name="Suburb names").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(panel_html(listings)))

    west, south, east, north = listings.total_bounds
    m.fit_bounds([[south, west], [north, east]])
    MapBehaviour(points, outlines, suburb_names, grey, street_names).add_to(m)
    return m

# ---------------------------------------------------------------------
# STATIC MAP
# ---------------------------------------------------------------------

# Outlines text so it stays readable over the listings.
HALO = [patheffects.withStroke(linewidth=3, foreground=SURFACE)]


def draw_extent(ax, extent):
    """Outline on one panel the area the next panel shows."""
    west, south, east, north = extent
    ax.add_patch(Rectangle(
        (west, south), east - west, north - south,
        fill=False, edgecolor=INK_SECONDARY, linewidth=0.8,
    ))


def draw_scale_bar(ax, extent, km):
    west, south, east, north = extent
    right = east - 0.04 * (east - west)
    y = south + 0.04 * (north - south)
    ax.plot([right - km * 1000, right], [y, y], color=INK_SECONDARY, linewidth=1.2,
            solid_capstyle="butt", path_effects=HALO)
    ax.annotate(f"{km} km", (right, y), xytext=(0, 3), textcoords="offset points",
                ha="right", va="bottom", fontsize=7, color=INK_SECONDARY, path_effects=HALO)


def draw_panel(ax, suburbs, listings, extent, title, places, point_size, boundary_width, scale_km):
    west, south, east, north = extent
    suburbs.plot(ax=ax, color=LAND_COLOUR, edgecolor=BOUNDARY_COLOUR, linewidth=boundary_width)
    ax.scatter(
        listings.geometry.x, listings.geometry.y,
        c=listings["rent_band"].map(BAND_COLOURS),
        s=point_size, linewidths=0, alpha=0.9,
    )
    # ABS adds the state to a name other states share, as in "Richmond (Vic.)".
    names = suburbs["sal_name_2021"].str.replace(r" \(.*\)$", "", regex=True)
    centres, _ = label_circles(suburbs.geometry[names.isin(places)])
    for place in places:
        centre = centres[names == place].iloc[0]
        # Above the suburb's centre, so the name does not sit on its listings.
        ax.annotate(
            place.removesuffix(" Central"), (centre.x, centre.y),
            xytext=(0, 5), textcoords="offset points", ha="center", va="bottom",
            fontsize=8, fontweight="bold", color=INK, path_effects=HALO, annotation_clip=True,
        )
    draw_scale_bar(ax, extent, scale_km)
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    ax.set_axis_off()


def plot_static_map(listings, suburbs, sa2, path):
    """
    Victoria, then Greater Melbourne, where 85% of the listings are, then the
    inner suburbs, each panel outlining the area the next one shows.
    """
    suburbs = suburbs.to_crs(MAP_CRS)
    # Shuffled so overlapping points show a fair mix of rent bands.
    points = listings.to_crs(MAP_CRS).sample(frac=1, random_state=0)

    victoria = suburbs.total_bounds
    # Greater Melbourne is an ABS region defined as a set of SA2s.
    melbourne = sa2[sa2["gccsa_name_2021"] == "Greater Melbourne"].to_crs(MAP_CRS).total_bounds
    in_melbourne = points["gccsa_name_2021"] == "Greater Melbourne"
    centre = suburbs.loc[suburbs["sal_name_2021"] == "Melbourne"].geometry.centroid.iloc[0]
    inner = centre.buffer(INNER_MELBOURNE_HALF_WIDTH_M).bounds
    in_inner = points.geometry.within(shapely.box(*inner))

    # Each panel's width is set from its extent, so all three come out the same height.
    aspects = [(e[2] - e[0]) / (e[3] - e[1]) for e in (victoria, melbourne, inner)]
    fig, (ax_vic, ax_melb, ax_inner) = plt.subplots(
        1, 3, figsize=(16, 6.2), width_ratios=aspects, layout="constrained"
    )
    fig.get_layout_engine().set(w_pad=0.12, h_pad=0.12)
    draw_panel(ax_vic, suburbs, points, victoria,
               f"Victoria: {len(points):,} listings",
               VICTORIA_PLACES, point_size=2, boundary_width=0.15, scale_km=100)
    draw_panel(ax_melb, suburbs, points, melbourne,
               f"Greater Melbourne: {in_melbourne.sum():,} listings",
               MELBOURNE_PLACES, point_size=2.5, boundary_width=0.2, scale_km=20)
    draw_panel(ax_inner, suburbs, points, inner,
               f"Inner Melbourne: {in_inner.sum():,} listings",
               INNER_MELBOURNE_PLACES, point_size=5, boundary_width=0.4, scale_km=5)
    draw_extent(ax_vic, melbourne)
    draw_extent(ax_melb, inner)

    handles = [
        # Escaped because matplotlib reads a pair of dollar signs as maths.
        Line2D([], [], marker="o", linestyle="", markersize=6, color=colour,
               label=label.replace("$", r"\$"))
        for label, colour in BAND_COLOURS.items()
    ]
    fig.legend(
        handles=handles, title="Advertised weekly rent", loc="outside lower center",
        ncols=len(handles), frameon=False, labelcolor=INK_SECONDARY,
    )
    fig.suptitle(
        "Where the rental listings are, on ABS suburb boundaries",
        x=0.01, ha="left", fontsize=14, color=INK,
    )
    fig.savefig(path, dpi=200, facecolor="white")
    plt.close(fig)

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DOMAIN_CSV))
    ap.add_argument("--output-dir", default=str(OUTPUT_DIR / "maps"))
    return ap.parse_args()


def main():
    args = parse_args()
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    # The mesh-block workbooks behind the suburb match are cached with the
    # pipeline's other downloads.
    cache = OUTPUT_DIR / "_cache"
    cache.mkdir(parents=True, exist_ok=True)

    sa2 = load_sa2()
    listings = load_located_listings(args.input, sa2, cache)
    suburbs = summarise_suburbs(load_sal_boundaries(), listings)

    html_path = outdir / "property_locations.html"
    build_interactive_map(listings, suburbs).save(html_path)
    print(f"Wrote interactive map of {len(listings):,} listings to {html_path}")

    png_path = outdir / "property_locations.png"
    plot_static_map(listings, suburbs, sa2, png_path)
    print(f"Wrote static map to {png_path}")


if __name__ == "__main__":
    main()

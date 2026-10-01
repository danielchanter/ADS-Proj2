"""
Map where the rental listings are.

Input:
    vic_rentals_all.csv

Output:
    output/maps/property_locations.html   interactive map, one point per listing
    output/maps/property_locations.png    the same points on the suburb boundaries

Both colour each listing by its advertised weekly rent and draw the ABS ASGS
2021 suburb (SAL) boundaries, the granularity the analysis is done at. The
script reads the raw Domain listings, so it runs on its own, ahead of
build_master_dataset.py.

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
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

from pipeline.abs_sources import load_sa2, load_sal_boundaries
from pipeline.common import DOMAIN_CSV, OUTPUT_DIR
from pipeline.correspondence import add_suburb_code, load_correspondence
from pipeline.listings import join_sa2, load_listings

# ---------------------------------------------------------------------
# RENT BANDS
# ---------------------------------------------------------------------

# Upper bound (exclusive), label and colour. The breaks sit near the 15th,
# 45th, 70th and 85th percentiles of weekly_rent, rounded to $50. weekly_rent
# is uncleaned, and banding puts the handful of listings above $5,000 a week
# in the top band with the rest of the expensive ones.
RENT_BANDS = [
    (450, "Under $450", "#86b6ef"),
    (550, "$450 to $549", "#5598e7"),
    (650, "$550 to $649", "#2a78d6"),
    (800, "$650 to $799", "#1c5cab"),
    (np.inf, "$800 or more", "#0d366b"),
]
NO_RENT_LABEL = "No rent listed"
NO_RENT_COLOUR = "#898781"
BAND_COLOURS = {label: colour for _, label, colour in RENT_BANDS} | {NO_RENT_LABEL: NO_RENT_COLOUR}

LAND_COLOUR = "#f0efec"
BOUNDARY_COLOUR = "#c3c2b7"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"

# VicGrid GDA2020, the equal-area CRS land.py measures in. Used to draw the
# static map undistorted and to simplify the boundaries in metres.
MAP_CRS = "EPSG:7899"

# The suburb boundaries are 57 MB as GeoJSON, too heavy to embed in a web page.
# Simplified as one coverage, so neighbouring suburbs keep a shared edge, they
# come to about 5 MB.
WEB_SIMPLIFY_M = 100


def rent_band(weekly_rent):
    """The RENT_BANDS label for each rent; a missing or zero rent is NO_RENT_LABEL."""
    rent = weekly_rent.where(weekly_rent > 0)
    band = pd.cut(
        rent,
        bins=[0] + [upper for upper, _, _ in RENT_BANDS],
        labels=[label for _, label, _ in RENT_BANDS],
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
    """Suburb polygons with the number of listings in each and their median rent."""
    rent = listings["weekly_rent"].where(listings["weekly_rent"] > 0)
    summary = rent.groupby(listings["sal_code_2021"]).agg(
        listing_count="size", median_weekly_rent="median"
    )
    out = suburbs.merge(summary, left_on="sal_code_2021", right_index=True, how="left")
    out["listing_count"] = out["listing_count"].fillna(0).astype(int)
    return out


def dollars(value):
    return "Not listed" if pd.isna(value) or value <= 0 else f"${value:,.0f}"


def describe_rooms(row):
    parts = [
        f"{row[col]:.0f} {unit}"
        for col, unit in [("bedrooms", "bed"), ("bathrooms", "bath"), ("carspaces", "car")]
        if pd.notna(row[col])
    ]
    return ", ".join(parts) or "Not listed"

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
            "type": listings["property_type"].fillna("Not listed"),
            "rooms": listings.apply(describe_rooms, axis=1),
            "colour": listings["rent_band"].map(BAND_COLOURS),
        },
        geometry=listings.geometry,
        crs=listings.crs,
    )
    return folium.GeoJson(
        shown,
        name="Rental listings",
        marker=folium.CircleMarker(radius=4, weight=0.5, color="#ffffff", fill=True, fill_opacity=0.9),
        style_function=lambda feature: {"fillColor": feature["properties"]["colour"]},
        tooltip=folium.GeoJsonTooltip(
            fields=["address", "suburb", "rent", "type", "rooms"],
            aliases=["Address", "Suburb", "Weekly rent", "Type", "Rooms"],
        ),
    )


def suburb_layer(suburb_summary):
    """Suburb outlines; hovering one gives its name, listing count and median rent."""
    shown = suburb_summary[["sal_name_2021", "listing_count", "median_weekly_rent"]].copy()
    shown["median_weekly_rent"] = shown["median_weekly_rent"].map(dollars)
    simplified = suburb_summary.geometry.to_crs(MAP_CRS).simplify_coverage(WEB_SIMPLIFY_M)
    # 5 dp is about 1 m, well within the 100 m simplification.
    shown = shown.set_geometry(shapely.set_precision(simplified.to_crs("EPSG:4326").values, 1e-5))
    shown = shown.set_crs("EPSG:4326")
    return folium.GeoJson(
        shown,
        name="Suburb boundaries",
        style_function=lambda feature: {"color": INK_SECONDARY, "weight": 0.8, "opacity": 0.7, "fillOpacity": 0},
        highlight_function=lambda feature: {"weight": 2, "fillOpacity": 0.08},
        tooltip=folium.GeoJsonTooltip(
            fields=["sal_name_2021", "listing_count", "median_weekly_rent"],
            aliases=["Suburb", "Listings", "Median weekly rent"],
        ),
    )


def legend_html(listings):
    counts = listings["rent_band"].value_counts()
    rows = "".join(
        f'<div style="display:flex;align-items:center;gap:8px;margin-top:4px">'
        f'<span style="width:12px;height:12px;border-radius:50%;background:{colour}"></span>'
        f'<span style="flex:1">{label}</span>'
        f'<span style="color:{INK_SECONDARY}">{counts.get(label, 0):,}</span></div>'
        for label, colour in BAND_COLOURS.items()
    )
    return (
        f'<div style="position:fixed;bottom:64px;left:10px;z-index:1000;min-width:190px;'
        f'padding:10px 12px;background:#fcfcfb;border:1px solid rgba(11,11,11,0.1);'
        f'border-radius:6px;color:{INK};font:12px system-ui,-apple-system,\'Segoe UI\',sans-serif">'
        f'<div style="font-weight:600">Victorian rental listings</div>'
        f'<div style="color:{INK_SECONDARY}">{len(listings):,} listings by weekly rent</div>'
        f'{rows}</div>'
    )


def build_interactive_map(listings, suburb_summary):
    # Canvas rendering draws all 12,000 circles on one element, which keeps
    # the map responsive.
    m = folium.Map(tiles=None, prefer_canvas=True, control_scale=True)
    # Faded so the listings stand out against the street map.
    folium.TileLayer("OpenStreetMap", name="Street map", opacity=0.6).add_to(m)
    suburb_layer(suburb_summary).add_to(m)
    listing_layer(listings).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(legend_html(listings)))

    west, south, east, north = listings.total_bounds
    m.fit_bounds([[south, west], [north, east]])
    return m

# ---------------------------------------------------------------------
# STATIC MAP
# ---------------------------------------------------------------------

def draw_panel(ax, suburbs, listings, extent, point_size, title):
    west, south, east, north = extent
    suburbs.plot(ax=ax, color=LAND_COLOUR, edgecolor=BOUNDARY_COLOUR, linewidth=0.2)
    ax.scatter(
        listings.geometry.x, listings.geometry.y,
        c=listings["rent_band"].map(BAND_COLOURS),
        s=point_size, linewidths=0, alpha=0.9,
    )
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    ax.set_axis_off()


def plot_static_map(listings, suburbs, sa2, path):
    """Victoria beside Greater Melbourne, where 85% of the listings are."""
    suburbs = suburbs.to_crs(MAP_CRS)
    # Shuffled so overlapping points show a fair mix of rent bands.
    points = listings.to_crs(MAP_CRS).sample(frac=1, random_state=0)

    victoria = suburbs.total_bounds
    # Greater Melbourne is an ABS region defined as a set of SA2s.
    melbourne = sa2[sa2["gccsa_name_2021"] == "Greater Melbourne"].to_crs(MAP_CRS).total_bounds
    in_melbourne = points["gccsa_name_2021"] == "Greater Melbourne"

    # Each panel's width is set from its extent, so both come out the same height.
    aspects = [(e[2] - e[0]) / (e[3] - e[1]) for e in (victoria, melbourne)]
    fig, (ax_vic, ax_melb) = plt.subplots(
        1, 2, figsize=(14, 7.3), width_ratios=aspects, layout="constrained"
    )
    fig.get_layout_engine().set(w_pad=0.15, h_pad=0.15)
    draw_panel(ax_vic, suburbs, points, victoria, 2,
               f"Victoria: {len(points):,} listings")
    draw_panel(ax_melb, suburbs, points, melbourne, 2.5,
               f"Greater Melbourne: {in_melbourne.sum():,} listings")
    ax_vic.add_patch(Rectangle(
        melbourne[:2], melbourne[2] - melbourne[0], melbourne[3] - melbourne[1],
        fill=False, edgecolor=INK_SECONDARY, linewidth=0.8,
    ))

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

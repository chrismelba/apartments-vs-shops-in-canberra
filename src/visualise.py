"""
Step 5: Generate the interactive HTML map.

Reads data/processed/analysis.csv and produces output/map.html — a Folium
map with one marker per shopping centre, coloured by shop quality score,
with popups showing score breakdown, density, and top-rated shops.

Usage:
    python src/visualise.py
"""

import json
import math
import os
import sys

import branca.colormap as cm
import folium
import pandas as pd
from folium.plugins import MarkerCluster

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import CANBERRA_CENTER, SCORE_WEIGHTS

PROC_DIR   = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
DOCS_DIR   = os.path.join(os.path.dirname(__file__), "..", "docs")


# ---------------------------------------------------------------------------
# Display name helper
# ---------------------------------------------------------------------------

# Brand prefixes to strip from Google Places names
_BRAND_PREFIXES = [
    "Woolworths Metro ", "Woolworths ", "Coles ", "ALDI ",
    "Supabarn ", "SupaBarn Express ", "SupaBarn ",
    "Supaexpress ", "SupaExpress ",
    "IGA ", "Friendly Grocer ", "Harris Farm Markets ",
    "Costco Wholesale ",
]

def display_name(row) -> str:
    """
    Return a clean display name for a centre:
      - Google Places: strip supermarket brand prefix  ("Woolworths Kippax" → "Kippax")
      - ACTMAPI local centres: strip "Local Centre — "  ("Local Centre — Holt" → "Holt")
    """
    name = str(row.get("name", "") or "")
    if str(row.get("source", "")) == "actmapi_cz4":
        return name.replace("Local Centre \u2014 ", "").replace("Local Centre - ", "").strip()
    for prefix in _BRAND_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return name


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def make_colormap(vmin=0, vmax=10):
    """Linear colormap: red → yellow → green, over the given data range."""
    return cm.LinearColormap(
        colors=["#d32f2f", "#f57c00", "#fbc02d", "#7cb342", "#2e7d32"],
        vmin=vmin,
        vmax=vmax,
        caption=f"Shop Quality Score ({vmin:.1f}–{vmax:.1f})",
    )


def marker_radius(num_shops, min_r=7, max_r=22):
    """Scale marker size to number of shops (log scale)."""
    if num_shops <= 0:
        return min_r
    return min(min_r + math.log(num_shops + 1) * 3, max_r)


# ---------------------------------------------------------------------------
# Popup HTML
# ---------------------------------------------------------------------------

def stars_html(rating):
    if rating is None:
        return "no rating"
    full  = int(rating)
    half  = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return "★" * full + "½" * half + "☆" * empty + f" {rating:.1f}"


def zone_bar_html(zone_breakdown_str: str) -> str:
    """Render a simple text-based zone breakdown."""
    try:
        breakdown = json.loads(zone_breakdown_str)
    except Exception:
        return ""
    if not breakdown:
        return "<p style='color:#888;font-size:11px'>No zoning data within radius</p>"

    total = sum(breakdown.values())
    rows = sorted(breakdown.items(), key=lambda x: -x[1])
    colours = {
        "RZ1": "#8bc34a", "RZ2": "#cddc39",
        "RZ3": "#ffc107", "RZ4": "#ff9800", "RZ5": "#f44336",
    }

    html = "<table style='width:100%;font-size:11px;border-collapse:collapse'>"
    for code, area in rows:
        pct = area / total * 100 if total else 0
        col = next((colours[k] for k in colours if code.startswith(k)), "#90a4ae")
        bar_w = max(int(pct * 1.2), 2)
        html += (
            f"<tr><td style='width:40px;padding:1px 3px'>{code}</td>"
            f"<td><div style='background:{col};width:{bar_w}px;height:10px;display:inline-block'></div></td>"
            f"<td style='padding-left:4px'>{pct:.0f}%</td></tr>"
        )
    html += "</table>"
    return html


def score_bar_html(label, score, colour="#1976d2"):
    bar_w = int(score * 14)
    return (
        f"<tr><td style='font-size:11px;padding:2px 4px;white-space:nowrap'>{label}</td>"
        f"<td><div style='background:{colour};width:{bar_w}px;height:10px;"
        f"display:inline-block;border-radius:2px'></div></td>"
        f"<td style='font-size:11px;padding-left:4px'>{score:.1f}</td></tr>"
    )


def build_popup(row: pd.Series, colormap, dname: str = "") -> folium.Popup:
    q  = row["shop_quality_score"]
    d  = row["density_score"]
    q_col = colormap(q)

    # Top shops
    try:
        tops = json.loads(row["top_shops"])
    except Exception:
        tops = []

    tops_html = ""
    for t in tops:
        tops_html += (
            f"<div style='margin:2px 0;font-size:12px'>"
            f"<b>{t['name']}</b> — {stars_html(t.get('rating'))}"
            f" <span style='color:#888'>({t.get('reviews',0):,} reviews)</span></div>"
        )

    # Zone breakdown
    zone_html = zone_bar_html(str(row.get("zone_breakdown", "{}")))

    # Score components
    comp_html = "<table style='width:100%;border-collapse:collapse'>"
    comp_html += score_bar_html("Avg rating",    row["score_avg_rating"],     "#43a047")
    comp_html += score_bar_html("Review density",row["score_review_density"], "#1e88e5")
    comp_html += score_bar_html("Variety",        row["score_variety"],        "#8e24aa")
    comp_html += score_bar_html("Hours coverage", row["score_hours"],          "#fb8c00")
    comp_html += "</table>"

    is_local = row.get("source") == "actmapi_cz4"
    centre_type_badge = (
        "<span style='background:#e3f2fd;color:#1565c0;font-size:10px;"
        "padding:1px 5px;border-radius:3px;margin-left:6px'>Local Centre</span>"
        if is_local else
        "<span style='background:#f3e5f5;color:#6a1b9a;font-size:10px;"
        "padding:1px 5px;border-radius:3px;margin-left:6px'>Group / Town Centre</span>"
    )

    # Population density row (optional — only shown when data available)
    pop_score = row.get("population_score")
    pop_500m  = row.get("population_500m")
    if pop_score is not None and not (isinstance(pop_score, float) and math.isnan(pop_score)):
        pop_html = (
            f"<hr style='margin:8px 0;border:none;border-top:1px solid #eee'>"
            f"<b style='font-size:12px'>Population density score: {float(pop_score):.1f}/10</b>"
            f"<p style='margin:2px 0 4px;font-size:11px;color:#666'>"
            f"~{int(pop_500m):,} residents within 500m (2021 Census)</p>"
        )
    else:
        pop_html = ""

    html = f"""
<div style="font-family:sans-serif;min-width:260px;max-width:320px">
  <div style="background:{q_col};color:white;padding:8px 12px;border-radius:4px 4px 0 0">
    <b style="font-size:14px">{dname or row['name']}</b>
    <span style="float:right;font-size:18px;font-weight:bold">{q:.1f}</span>
  </div>
  <div style="padding:8px 12px;border:1px solid #eee;border-top:none">

    <p style="margin:4px 0 6px;font-size:11px;color:#555">
      {centre_type_badge}&nbsp; {row.get('address','')}
    </p>

    <b style="font-size:12px">Shop quality score: {q:.1f}/10</b>
    <p style="margin:2px 0 4px;font-size:11px;color:#666">
      {row['num_shops']} shops &bull; {int(row['total_reviews']):,} total reviews
    </p>
    {comp_html}

    <hr style="margin:8px 0;border:none;border-top:1px solid #eee">
    <b style="font-size:12px">Zoning density score: {d:.1f}/10</b>
    <p style="margin:2px 0 4px;font-size:11px;color:#666">
      Residential zone avg: {row['rz_avg']:.2f}/5 &bull;
      {row['residential_coverage']:.0%} of 500m buffer is residential
    </p>
    {zone_html}
    {pop_html}

    <hr style="margin:8px 0;border:none;border-top:1px solid #eee">
    <b style="font-size:12px">Top rated shops nearby</b>
    {tops_html if tops_html else '<p style="color:#888;font-size:11px">No rated shops found</p>'}
  </div>
</div>
"""
    return folium.Popup(html, max_width=340)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    csv_path = os.path.join(PROC_DIR, "analysis.csv")
    if not os.path.exists(csv_path):
        sys.exit("Run analyse.py first.")

    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} centres")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Map setup
    m = folium.Map(
        location=[CANBERRA_CENTER["lat"], CANBERRA_CENTER["lng"]],
        zoom_start=12,
        tiles="CartoDB positron",
    )

    vmin = df["shop_quality_score"].quantile(0.05)
    vmax = df["shop_quality_score"].quantile(0.95)
    colormap = make_colormap(vmin=vmin, vmax=vmax)
    colormap.add_to(m)

    # Separate feature groups so user can toggle each type
    group_centres_group = folium.FeatureGroup(name="Group / Town Centres", show=True)
    local_centres_group = folium.FeatureGroup(name="Local Centres (CZ4)", show=True)

    for _, row in df.iterrows():
        if pd.isna(row["lat"]) or pd.isna(row["lng"]):
            continue

        is_local = row.get("source") == "actmapi_cz4"
        q        = row["shop_quality_score"]
        colour   = colormap(q)

        if is_local:
            # Local centres: smaller, hollow-ish, dashed outline
            radius       = marker_radius(row["num_shops"], min_r=4, max_r=11)
            stroke_color = "#555"
            stroke_weight = 1.5
            fill_opacity  = 0.70
            dash_array    = "4 3"
            target_group  = local_centres_group
        else:
            # Group/town centres: larger, solid, white outline
            radius        = marker_radius(row["num_shops"])
            stroke_color  = "white"
            stroke_weight = 2
            fill_opacity  = 0.88
            dash_array    = None
            target_group  = group_centres_group

        dname = display_name(row)
        folium.CircleMarker(
            location=[row["lat"], row["lng"]],
            radius=radius,
            color=stroke_color,
            weight=stroke_weight,
            dash_array=dash_array,
            fill=True,
            fill_color=colour,
            fill_opacity=fill_opacity,
            popup=build_popup(row, colormap, dname),
            tooltip=folium.Tooltip(
                f"<b>{dname}</b><br>"
                f"Quality: {q:.1f} &nbsp;|&nbsp; Density: {row['density_score']:.1f}<br>"
                f"{'Local Centre' if is_local else 'Group/Town Centre'} &bull; "
                f"{int(row['num_shops'])} shops",
                sticky=True,
            ),
        ).add_to(target_group)

    group_centres_group.add_to(m)
    local_centres_group.add_to(m)

    # Scatter plot inset (quality vs density) as an SVG legend
    _add_scatter_legend(m, df)

    # Layer control
    folium.LayerControl().add_to(m)

    # Size legend
    _add_size_legend(m)

    os.makedirs(DOCS_DIR, exist_ok=True)
    out_path = os.path.join(DOCS_DIR, "index.html")
    m.save(out_path)
    print(f"Map saved → {os.path.relpath(out_path)}")
    print("Open docs/index.html in your browser, or visit the GitHub Pages URL.")


def _add_size_legend(m):
    legend_html = """
    <div style="position:fixed;bottom:50px;left:50px;z-index:1000;background:white;
                padding:10px 14px;border-radius:6px;box-shadow:0 2px 6px rgba(0,0,0,.3);
                font-family:sans-serif;font-size:12px">
      <b>Marker size = number of shops</b><br>
      <svg width="120" height="60">
        <circle cx="15" cy="45" r="7"  fill="#888" opacity="0.7"/>
        <circle cx="45" cy="40" r="12" fill="#888" opacity="0.7"/>
        <circle cx="85" cy="33" r="18" fill="#888" opacity="0.7"/>
        <text x="15" y="58" text-anchor="middle" font-size="9">few</text>
        <text x="45" y="58" text-anchor="middle" font-size="9">some</text>
        <text x="85" y="58" text-anchor="middle" font-size="9">many</text>
      </svg>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))


def _add_scatter_legend(m, df):
    """Small always-visible scatter chart in the corner.

    Clicking the expand icon (⛶) opens a modal overlay (~85vw x 85vh) with
    the same chart drawn large, with hover tooltips on every dot.
    """
    if len(df) == 0:
        return

    def _safe_float(val):
        try:
            f = float(val)
            return None if math.isnan(f) else round(f, 1)
        except (TypeError, ValueError):
            return None

    points_json = json.dumps([
        {
            "name":   display_name(row).replace("'", "\\'"),
            "q":      round(float(row["shop_quality_score"]), 1),
            "d":      round(float(row["density_score"]), 1),
            "p":      _safe_float(row.get("population_score")),
            "pop500": int(row["population_500m"]) if row.get("population_500m") not in (None, "") and not (isinstance(row.get("population_500m"), float) and math.isnan(row["population_500m"])) else 0,
            "local":  str(row.get("source", "")) == "actmapi_cz4",
        }
        for _, row in df.iterrows()
    ])

    # Detect whether population data exists in this dataset
    has_population = df["population_score"].notna().any() if "population_score" in df.columns else False
    toggle_html = (
        """
  <!-- Y-axis toggle (only shown when population data available) -->
  <div id="sc-toggle"
       style="display:flex;gap:0;margin-bottom:5px;border-radius:4px;overflow:hidden;
              border:1px solid #ddd;width:fit-content">
    <button id="sc-btn-zoning" onclick="scatterSetMode('zoning')"
            style="border:none;padding:3px 10px;font-size:10px;cursor:pointer;
                   background:#1976d2;color:white;font-weight:bold">
      Zoning density
    </button>
    <button id="sc-btn-pop" onclick="scatterSetMode('population')"
            style="border:none;padding:3px 10px;font-size:10px;cursor:pointer;
                   background:#f5f5f5;color:#555">
      Population density
    </button>
  </div>"""
        if has_population else ""
    )

    html = f"""
<!-- ── Scatter: mini panel ──────────────────────────────────── -->
<div id="sc-mini"
     style="position:fixed;top:60px;right:20px;z-index:1000;background:white;
            border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.3);
            font-family:sans-serif;padding:8px 10px 6px;user-select:none">

  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
    <span id="sc-mini-title" style="font-size:11px;font-weight:bold;color:#333">Quality vs Zoning Density</span>
    <button onclick="scatterOpen()" title="Expand to full screen"
            style="border:none;background:none;cursor:pointer;font-size:15px;color:#666;
                   padding:0 0 0 8px;line-height:1">&#x26F6;</button>
  </div>

  <svg id="sc-mini-svg" width="190" height="160"
       style="display:block;overflow:visible;cursor:pointer" onclick="scatterOpen()">
  </svg>

  <div style="font-size:9px;color:#999;margin-top:3px;display:flex;gap:8px;align-items:center">
    <span style="color:#1976d2">&#9679;</span> Group/Town &nbsp;
    <span style="color:#e57373">&#9679;</span> Local
    <span style="margin-left:auto;color:#bbb">click to expand</span>
  </div>
</div>

<!-- ── Scatter: fullscreen modal ────────────────────────────── -->
<div id="sc-modal" onclick="scatterClose(event)"
     style="display:none;position:fixed;inset:0;z-index:3000;
            background:rgba(0,0,0,0.55);align-items:center;justify-content:center">

  <div onclick="event.stopPropagation()"
       style="background:white;border-radius:10px;padding:20px 24px 14px;
              width:85vw;height:85vh;box-sizing:border-box;
              display:flex;flex-direction:column;
              box-shadow:0 8px 32px rgba(0,0,0,.45)">

    <div style="display:flex;align-items:center;justify-content:space-between;
                flex-shrink:0;margin-bottom:10px">
      <div style="display:flex;align-items:center;gap:14px">
        <span id="sc-modal-title" style="font-size:15px;font-weight:bold;color:#333">
          Quality vs Zoning Density — all centres
        </span>
        {toggle_html}
      </div>
      <div style="display:flex;gap:16px;align-items:center">
        <span style="font-size:11px;color:#666;display:flex;gap:14px">
          <span><svg width="11" height="11" style="vertical-align:middle">
            <circle cx="5.5" cy="5.5" r="4.5" fill="#1976d2" opacity="0.75"/>
          </svg> Group / Town Centre</span>
          <span><svg width="11" height="11" style="vertical-align:middle">
            <circle cx="5.5" cy="5.5" r="4.5" fill="#e57373" opacity="0.75"/>
          </svg> Local Centre (CZ4)</span>
        </span>
        <button onclick="scatterClose(event)"
                style="border:none;background:#eee;cursor:pointer;font-size:18px;
                       color:#555;width:28px;height:28px;border-radius:50%;
                       line-height:28px;padding:0">&times;</button>
      </div>
    </div>

    <svg id="sc-full-svg" style="flex:1;width:100%;overflow:visible"></svg>
  </div>
</div>

<!-- Shared floating tooltip -->
<div id="sc-tip"
     style="display:none;position:fixed;background:rgba(25,25,25,0.88);color:white;
            font-size:12px;padding:6px 10px;border-radius:5px;pointer-events:none;
            z-index:4000;line-height:1.6;max-width:240px">
</div>

<script>
(function() {{
  var DATA = {points_json};
  var SC_MODE = 'zoning';   // 'zoning' | 'population'
  // Mirrors Leaflet layer visibility — keys match the local bool in DATA
  var SC_VISIBLE = {{ group: true, local: true }};

  var SVG_NS = 'http://www.w3.org/2000/svg';
  function el(tag) {{ return document.createElementNS(SVG_NS, tag); }}

  // ── linear regression helper ──────────────────────────────────
  function linreg(pts) {{
    // pts: [{{x, y}}, ...]  — returns {{slope, intercept, r2}} or null if < 2 points
    var n = pts.length;
    if (n < 2) return null;
    var sx = 0, sy = 0, sxy = 0, sxx = 0, syy = 0;
    pts.forEach(function(p) {{
      sx  += p.x; sy  += p.y;
      sxy += p.x * p.y;
      sxx += p.x * p.x;
      syy += p.y * p.y;
    }});
    var denom = n * sxx - sx * sx;
    if (denom === 0) return null;
    var slope = (n * sxy - sx * sy) / denom;
    var intercept = (sy - slope * sx) / n;
    // R²
    var yMean = sy / n;
    var ssTot = 0, ssRes = 0;
    pts.forEach(function(p) {{
      ssTot += (p.y - yMean) * (p.y - yMean);
      var yHat = slope * p.x + intercept;
      ssRes += (p.y - yHat) * (p.y - yHat);
    }});
    var r2 = ssTot > 0 ? 1 - ssRes / ssTot : 0;
    return {{ slope: slope, intercept: intercept, r2: r2 }};
  }}

  function yVal(d) {{
    if (SC_MODE === 'population') return (d.p !== null && d.p !== undefined) ? d.p : null;
    return d.d;
  }}

  function isVisible(d) {{
    if (d.pop500 < 50) return false;  // exclude centres with negligible population within 500m
    return d.local ? SC_VISIBLE.local : SC_VISIBLE.group;
  }}

  // ── draw chart into an <svg> element ─────────────────────────
  function drawChart(svgEl, rNorm, rHover, fontSize) {{
    while (svgEl.firstChild) svgEl.removeChild(svgEl.firstChild);

    var rect = svgEl.getBoundingClientRect();
    var W = rect.width  || 500;
    var H = rect.height || 400;
    svgEl.setAttribute('viewBox', '0 0 ' + W + ' ' + H);

    var padL = Math.round(W * 0.09);
    var padB = Math.round(H * 0.12);
    var padT = Math.round(H * 0.05);
    var padR = Math.round(W * 0.03);

    function sx(v) {{ return padL + (v / 10) * (W - padL - padR); }}
    function sy(v) {{ return H - padB - (v / 10) * (H - padB - padT); }}

    // faint grid
    [2,4,6,8].forEach(function(v) {{
      var g1 = el('line');
      g1.setAttribute('x1', sx(v)); g1.setAttribute('y1', padT);
      g1.setAttribute('x2', sx(v)); g1.setAttribute('y2', H - padB);
      g1.setAttribute('stroke', '#f0f0f0'); g1.setAttribute('stroke-width', '1');
      svgEl.appendChild(g1);
      var g2 = el('line');
      g2.setAttribute('x1', padL); g2.setAttribute('y1', sy(v));
      g2.setAttribute('x2', W - padR); g2.setAttribute('y2', sy(v));
      g2.setAttribute('stroke', '#f0f0f0'); g2.setAttribute('stroke-width', '1');
      svgEl.appendChild(g2);
    }});

    // axes
    var ax = el('line');
    ax.setAttribute('x1', padL); ax.setAttribute('y1', padT);
    ax.setAttribute('x2', padL); ax.setAttribute('y2', H - padB);
    ax.setAttribute('stroke', '#bbb'); ax.setAttribute('stroke-width', '1.5');
    svgEl.appendChild(ax);
    var ay = el('line');
    ay.setAttribute('x1', padL); ay.setAttribute('y1', H - padB);
    ay.setAttribute('x2', W - padR); ay.setAttribute('y2', H - padB);
    ay.setAttribute('stroke', '#bbb'); ay.setAttribute('stroke-width', '1.5');
    svgEl.appendChild(ay);

    // tick labels
    [0,2,4,6,8,10].forEach(function(v) {{
      var tx = el('text');
      tx.setAttribute('x', sx(v)); tx.setAttribute('y', H - padB + fontSize + 3);
      tx.setAttribute('text-anchor', 'middle');
      tx.setAttribute('font-size', fontSize); tx.setAttribute('fill', '#999');
      tx.textContent = v;
      svgEl.appendChild(tx);
      var ty = el('text');
      ty.setAttribute('x', padL - 5); ty.setAttribute('y', sy(v) + fontSize * 0.35);
      ty.setAttribute('text-anchor', 'end');
      ty.setAttribute('font-size', fontSize); ty.setAttribute('fill', '#999');
      ty.textContent = v;
      svgEl.appendChild(ty);
    }});

    // axis labels
    var lx = el('text');
    lx.setAttribute('x', (padL + W - padR) / 2);
    lx.setAttribute('y', H - 3);
    lx.setAttribute('text-anchor', 'middle');
    lx.setAttribute('font-size', fontSize + 1); lx.setAttribute('fill', '#666');
    lx.textContent = 'Shop Quality \u2192';
    svgEl.appendChild(lx);
    var yLabel = SC_MODE === 'population' ? 'Population Density \u2192' : 'Zoning Density \u2192';
    var midY = (padT + H - padB) / 2;
    var ly = el('text');
    ly.setAttribute('x', 9); ly.setAttribute('y', midY);
    ly.setAttribute('text-anchor', 'middle');
    ly.setAttribute('font-size', fontSize + 1); ly.setAttribute('fill', '#666');
    ly.setAttribute('transform', 'rotate(-90,9,' + midY + ')');
    ly.textContent = yLabel;
    svgEl.appendChild(ly);

    // ── trend line + R² ─────────────────────────────────────────
    var regPts = [];
    DATA.forEach(function(d) {{
      var yv = yVal(d);
      if (yv === null || yv === undefined) return;
      if (!isVisible(d)) return;
      regPts.push({{ x: d.q, y: yv }});
    }});
    var reg = linreg(regPts);
    if (reg) {{
      // Clamp trend line to x-axis range [0, 10]
      var x0 = 0,  y0 = reg.slope * x0 + reg.intercept;
      var x1 = 10, y1 = reg.slope * x1 + reg.intercept;
      // Clamp y to [0, 10] to keep line inside axes
      if (y0 < 0)  {{ x0 = -reg.intercept / reg.slope; y0 = 0; }}
      if (y0 > 10) {{ x0 = (10 - reg.intercept) / reg.slope; y0 = 10; }}
      if (y1 < 0)  {{ x1 = -reg.intercept / reg.slope; y1 = 0; }}
      if (y1 > 10) {{ x1 = (10 - reg.intercept) / reg.slope; y1 = 10; }}

      var tl = el('line');
      tl.setAttribute('x1', sx(x0)); tl.setAttribute('y1', sy(y0));
      tl.setAttribute('x2', sx(x1)); tl.setAttribute('y2', sy(y1));
      tl.setAttribute('stroke', '#ff6f00');
      tl.setAttribute('stroke-width', fontSize > 9 ? '2' : '1.2');
      tl.setAttribute('stroke-dasharray', fontSize > 9 ? '6 3' : '4 2');
      tl.setAttribute('opacity', '0.85');
      svgEl.appendChild(tl);

      // R² label — top-right of chart area
      var r2txt = el('text');
      r2txt.setAttribute('x', W - padR - 4);
      r2txt.setAttribute('y', padT + fontSize + 2);
      r2txt.setAttribute('text-anchor', 'end');
      r2txt.setAttribute('font-size', fontSize);
      r2txt.setAttribute('fill', '#ff6f00');
      r2txt.setAttribute('font-weight', 'bold');
      r2txt.textContent = 'R\u00B2 = ' + reg.r2.toFixed(2);
      svgEl.appendChild(r2txt);
    }}

    // dots (drawn on top of trend line)
    DATA.forEach(function(d) {{
      var yv = yVal(d);
      if (yv === null || yv === undefined) return;  // skip if no data for this mode
      if (!isVisible(d)) return;  // skip if layer is toggled off
      var c = el('circle');
      c.setAttribute('cx', sx(d.q));
      c.setAttribute('cy', sy(yv));
      c.setAttribute('r',  rNorm);
      c.setAttribute('fill',         d.local ? '#e57373' : '#1976d2');
      c.setAttribute('opacity',      '0.72');
      c.setAttribute('stroke',       'white');
      c.setAttribute('stroke-width', '0.8');
      c.dataset.name  = d.name;
      c.dataset.q     = d.q;
      c.dataset.yv    = yv;
      c.dataset.rn    = rNorm;
      c.dataset.rh    = rHover;
      c.classList.add('sc-dot');
      svgEl.appendChild(c);
    }});
  }}

  // ── tooltip (event delegation on the SVG) ────────────────────
  var tip = null;
  function getTip() {{
    if (!tip) tip = document.getElementById('sc-tip');
    return tip;
  }}

  var yAxisLabel = {{ zoning: 'Zoning density', population: 'Population density' }};

  function attachTips(svgEl) {{
    svgEl.addEventListener('mouseover', function(e) {{
      var dot = e.target;
      if (!dot.classList || !dot.classList.contains('sc-dot')) return;
      var t = getTip();
      t.innerHTML = '<b>' + dot.dataset.name + '</b><br>'
                  + 'Quality: ' + dot.dataset.q + '/10<br>'
                  + yAxisLabel[SC_MODE] + ': ' + dot.dataset.yv + '/10';
      t.style.display = 'block';
      dot.setAttribute('r', dot.dataset.rh);
      dot.setAttribute('opacity', '1');
      moveTip(e);
    }});
    svgEl.addEventListener('mousemove', function(e) {{
      if (e.target.classList && e.target.classList.contains('sc-dot')) moveTip(e);
    }});
    svgEl.addEventListener('mouseout', function(e) {{
      var dot = e.target;
      if (!dot.classList || !dot.classList.contains('sc-dot')) return;
      getTip().style.display = 'none';
      dot.setAttribute('r', dot.dataset.rn);
      dot.setAttribute('opacity', '0.72');
    }});
  }}

  function moveTip(e) {{
    var t = getTip();
    var x = e.clientX + 16, y = e.clientY - 14;
    if (x + 248 > window.innerWidth)  x = e.clientX - 256;
    if (y + 80  > window.innerHeight) y = e.clientY - 80;
    t.style.left = x + 'px';
    t.style.top  = y + 'px';
  }}

  // ── Y-axis mode toggle ────────────────────────────────────────
  var BTN_ACTIVE   = {{ background: '#1976d2', color: 'white', fontWeight: 'bold' }};
  var BTN_INACTIVE = {{ background: '#f5f5f5', color: '#555',  fontWeight: 'normal' }};

  function applyBtnStyle(btn, active) {{
    Object.assign(btn.style, active ? BTN_ACTIVE : BTN_INACTIVE);
  }}

  window.scatterSetMode = function(mode) {{
    SC_MODE = mode;
    var titles = {{
      zoning:     'Quality vs Zoning Density — all centres',
      population: 'Quality vs Population Density — all centres',
    }};
    var miniTitles = {{
      zoning:     'Quality vs Zoning Density',
      population: 'Quality vs Population Density',
    }};
    var mt = document.getElementById('sc-modal-title');
    var mn = document.getElementById('sc-mini-title');
    if (mt) mt.textContent = titles[mode];
    if (mn) mn.textContent = miniTitles[mode];

    var bz = document.getElementById('sc-btn-zoning');
    var bp = document.getElementById('sc-btn-pop');
    if (bz) applyBtnStyle(bz, mode === 'zoning');
    if (bp) applyBtnStyle(bp, mode === 'population');

    // Redraw both charts
    var fullSvg = document.getElementById('sc-full-svg');
    if (fullSvg && document.getElementById('sc-modal').style.display !== 'none') {{
      drawChart(fullSvg, 7, 11, 12);
      attachTips(fullSvg);
    }}
    var miniSvg = document.getElementById('sc-mini-svg');
    if (miniSvg) drawChart(miniSvg, 3.5, 5.5, 8);
  }};

  // ── open / close ─────────────────────────────────────────────
  window.scatterOpen = function() {{
    var modal = document.getElementById('sc-modal');
    modal.style.display = 'flex';
    var svg = document.getElementById('sc-full-svg');
    setTimeout(function() {{
      drawChart(svg, 7, 11, 12);
      attachTips(svg);
    }}, 30);
  }};

  window.scatterClose = function(e) {{
    document.getElementById('sc-modal').style.display = 'none';
    getTip().style.display = 'none';
  }};

  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') scatterClose(e);
  }});

  // ── mini chart on load ───────────────────────────────────────
  function initMini() {{
    var svg = document.getElementById('sc-mini-svg');
    if (!svg || !svg.getBoundingClientRect().width) {{
      setTimeout(initMini, 100);
      return;
    }}
    drawChart(svg, 3.5, 5.5, 8);
    hookLeafletLayers();
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initMini);
  }} else {{
    setTimeout(initMini, 60);
  }}

  // ── sync with Leaflet layer control ──────────────────────────
  // Layer names must match the FeatureGroup names in visualise.py main()
  var LAYER_MAP = {{
    'Group / Town Centres': 'group',
    'Local Centres (CZ4)':  'local',
  }};

  function redrawAll() {{
    var miniSvg = document.getElementById('sc-mini-svg');
    if (miniSvg) drawChart(miniSvg, 3.5, 5.5, 8);
    var fullSvg = document.getElementById('sc-full-svg');
    if (fullSvg && document.getElementById('sc-modal').style.display !== 'none') {{
      drawChart(fullSvg, 7, 11, 12);
      attachTips(fullSvg);
    }}
  }}

  function hookLeafletLayers() {{
    // Find the Leaflet map instance on window
    var leafletMap = null;
    for (var key in window) {{
      try {{
        if (window[key] && window[key].hasLayer && window[key].on) {{
          leafletMap = window[key];
          break;
        }}
      }} catch(e) {{}}
    }}
    if (!leafletMap) return;

    leafletMap.on('overlayadd', function(e) {{
      var key = LAYER_MAP[e.name];
      if (key) {{ SC_VISIBLE[key] = true;  redrawAll(); }}
    }});
    leafletMap.on('overlayremove', function(e) {{
      var key = LAYER_MAP[e.name];
      if (key) {{ SC_VISIBLE[key] = false; redrawAll(); }}
    }});
  }}
}})();
</script>
"""
    m.get_root().html.add_child(folium.Element(html))


if __name__ == "__main__":
    main()

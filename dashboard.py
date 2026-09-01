"""Streamlit dashboard over the SQLite snapshots. Run with:
    streamlit run dashboard.py

Layout follows what the data actually supports. Price and catalog membership
are fully populated every run, so they lead. Stock/rating/badges depend on
getting past Halfords' bot protection and are frequently absent: the "Ratings
& reviews" tab draws them when a snapshot has them and shows an explainer when
it doesn't, and "Data health" always reports the gap as a gap rather than an
empty chart. See README "About the badge pass" for why the distinction matters.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DB_PATH = Path(__file__).parent / "data" / "apollo_dashboard.db"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Palette: the validated reference instance from the dataviz skill. Categorical
# slots are used in fixed order and never cycled; single-series charts always
# take slot 1. Diverging (price up/down) uses the blue<->red pair - not the
# status palette, since a price move isn't inherently good or bad.
LIGHT = {
    "surface": "#fcfcfb", "text_primary": "#0b0b0b", "text_secondary": "#52514e",
    "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
    "border": "rgba(11,11,11,0.10)",
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
               "#008300", "#4a3aa7", "#e34948"],
    "down": "#2a78d6", "up": "#d03b3b",
}
DARK = {
    "surface": "#1a1a19", "text_primary": "#ffffff", "text_secondary": "#c3c2b7",
    "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835",
    "border": "rgba(255,255,255,0.10)",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181",
               "#008300", "#9085e9", "#e66767"],
    "down": "#3987e5", "up": "#d03b3b",
}

st.set_page_config(page_title="Apollo Distributor Watch", layout="wide")


def active_theme() -> str:
    try:
        t = st.context.theme.type
        if t in ("light", "dark"):
            return t
    except Exception:
        pass
    base = st.get_option("theme.base")
    return base if base in ("light", "dark") else "light"


P = DARK if active_theme() == "dark" else LIGHT


def style_fig(fig, height: int = 320, xgrid: bool = False, ygrid: bool = True):
    """Recessive chrome: hairline solid grid one shade off the surface, no
    zerolines, transparent plot area so it sits on Streamlit's own surface."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13, color=P["text_secondary"]),
        margin=dict(l=4, r=4, t=4, b=4), height=height,
        hoverlabel=dict(bgcolor=P["surface"], bordercolor=P["border"],
                        font=dict(family=FONT, color=P["text_primary"], size=12)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    font=dict(color=P["text_secondary"], size=12),
                    bgcolor="rgba(0,0,0,0)"),
        showlegend=False,
    )
    # automargin: category names on a horizontal bar and the x-axis tick band
    # need room the fixed margin doesn't reserve. Without it plotly silently
    # clips the tick labels rather than shrinking the plot.
    fig.update_xaxes(showgrid=xgrid, gridcolor=P["grid"], gridwidth=1, zeroline=False,
                     linecolor=P["axis"], linewidth=1, automargin=True,
                     tickfont=dict(color=P["muted"], size=12), title_font=dict(size=12))
    fig.update_yaxes(showgrid=ygrid, gridcolor=P["grid"], gridwidth=1, zeroline=False,
                     linecolor="rgba(0,0,0,0)", automargin=True,
                     tickfont=dict(color=P["muted"], size=12), title_font=dict(size=12))
    return fig


@st.cache_data(ttl=300)
def load_data():
    conn = sqlite3.connect(DB_PATH)
    products = pd.read_sql_query("SELECT * FROM products", conn)
    snapshots = pd.read_sql_query("SELECT * FROM snapshots", conn)
    try:
        runs = pd.read_sql_query("SELECT * FROM run_log ORDER BY run_id DESC LIMIT 15", conn)
    except Exception:
        runs = pd.DataFrame()
    conn.close()
    if not snapshots.empty:
        snapshots["snapshot_date"] = pd.to_datetime(snapshots["snapshot_date"])
    return products, snapshots, runs


def money(v) -> str:
    return "n/a" if pd.isna(v) else f"£{v:,.0f}"


def short_titles(titles: pd.Series) -> pd.Series:
    """Model name only - drop a trailing ' - M, L Frames' / ' - 20" Wheel'. The
    separator is space-dash-space; the bare hyphen in 'Phaze-E' must survive."""
    return titles.str.replace(r"\s+-\s+.*$", "", regex=True).str.slice(0, 44)


def hbar_categories(fig, labels):
    """Plot a horizontal bar against a positional y axis, then relabel the ticks.
    Passing the (possibly duplicated) label strings straight to `y=` makes plotly
    collapse same-named rows into one stacked bar - 'Apollo Phaze' x3 rendered as
    a single £1,725 bar. Positional y keeps every row distinct."""
    labels = list(labels)
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(labels))),
                     ticktext=labels)
    return list(range(len(labels)))


st.title("Apollo Bikes — Distributor Watch")

if not DB_PATH.exists():
    st.warning("No database yet. Run `python run_daily.py` first.")
    st.stop()

products, snapshots, runs = load_data()
if products.empty or snapshots.empty:
    st.warning("No data yet. Run `python run_daily.py` first.")
    st.stop()

merged = snapshots.merge(products, on=["distributor", "pid"], suffixes=("", "_p"))
merged["category_path"] = merged["category_path"].fillna("(uncategorised)")

# ---------------------------------------------------------------- filters ---
with st.sidebar:
    st.header("Filters")
    distributors = sorted(merged["distributor"].unique())
    sel_dist = st.multiselect("Distributor", distributors, default=distributors)
    categories = sorted(merged["category_path"].unique())
    sel_cat = st.multiselect("Category", categories, default=categories)
    search = st.text_input("Search model", placeholder="e.g. Gridlok")
    st.divider()
    st.caption(
        "Cached 5 min from the local SQLite file.\n\n"
        "`python run_daily.py` — catalog, price, badges\n\n"
        "`--enrich` — also stock/rating (best-effort; often blocked)"
    )

filtered = merged[merged["distributor"].isin(sel_dist) & merged["category_path"].isin(sel_cat)]
if search:
    filtered = filtered[filtered["title"].str.contains(search, case=False, na=False)]

if filtered.empty:
    st.info("No rows match the current filters.")
    st.stop()

dates = sorted(filtered["snapshot_date"].unique())
latest_date = dates[-1]
prev_date = dates[-2] if len(dates) > 1 else None

# Rating / reviews / badges / stock come only from the manual local enrich pass
# (Halfords blocks automated browsers, so the daily cloud refresh is price-only).
# For the "latest" view, carry each of those forward from the most recent
# snapshot that actually observed it - otherwise the dashboard shows n/a for
# ratings on every day between local runs. Trends and Data health keep raw
# per-snapshot values; only `latest` / `prev` are filled.
ENRICH_COLS = ["rating", "review_count", "in_stock", "merch_badges"]


def carry_forward(frame, as_of):
    """Returns the frame with enrich columns filled, plus {col: last-observed
    date} so callers can say how stale each field is (badges and ratings often
    have different ages - a badge pass can succeed on a day enrich doesn't)."""
    frame = frame.copy()
    hist = filtered[filtered["snapshot_date"] <= as_of].sort_values("snapshot_date")
    stamps = {}
    for col in ENRICH_COLS:
        seen = hist[hist[col].notna()]
        if seen.empty:
            continue
        frame[col] = frame[col].fillna(frame["pid"].map(seen.groupby("pid")[col].last()))
        stamps[col] = seen["snapshot_date"].max()
    return frame, stamps


latest, enrich_stamps = carry_forward(filtered[filtered["snapshot_date"] == latest_date],
                                      latest_date)
prev = None
if prev_date is not None:
    prev, _ = carry_forward(filtered[filtered["snapshot_date"] == prev_date], prev_date)

rating_as_of = enrich_stamps.get("rating")
rating_stale = rating_as_of is not None and pd.Timestamp(rating_as_of) < pd.Timestamp(latest_date)

days_old = (pd.Timestamp.now().normalize() - pd.Timestamp(latest_date)).days
freshness = "today" if days_old == 0 else ("yesterday" if days_old == 1 else f"{days_old} days ago")
if rating_as_of is None:
    enrich_note = " · no ratings collected yet"
elif rating_stale:
    enrich_note = f" · ratings as of **{pd.Timestamp(rating_as_of).date()}** (price-only since)"
else:
    enrich_note = ""
st.caption(f"Latest snapshot **{pd.Timestamp(latest_date).date()}** ({freshness}) · "
           f"{len(dates)} snapshot{'s' if len(dates) != 1 else ''} on file · "
           f"{filtered['pid'].nunique()} models ever seen{enrich_note}")


def delta_or_none(cur, before):
    if before is None or pd.isna(before) or pd.isna(cur):
        return None
    d = cur - before
    return None if d == 0 else d


n_now = int(latest["pid"].nunique())
n_prev = int(prev["pid"].nunique()) if prev is not None else None
med_now = latest["sale_price"].median()
med_prev = prev["sale_price"].median() if prev is not None else None
disc_now = latest[latest["discount_pct"].fillna(0) > 0]
disc_prev = prev[prev["discount_pct"].fillna(0) > 0] if prev is not None else None
median_series = filtered.groupby("snapshot_date")["sale_price"].median().tolist()
# A sparkline needs enough points to have a shape, and some variation to show.
# Below that it renders as an empty box that unbalances the tile row.
spark = median_series if (len(median_series) >= 3 and len(set(median_series)) > 1) else None

c1, c2, c3, c4 = st.columns(4)
TILE_H = 150  # equal height: a delta row otherwise makes some cards taller
c1.metric("Models listed", n_now, delta=delta_or_none(n_now, n_prev), border=True, height=TILE_H,
          help="Distinct products in the latest snapshot, after filters.")
c2.metric("Median price", money(med_now),
          delta=(f"£{med_now - med_prev:+,.0f}" if delta_or_none(med_now, med_prev) else None),
          border=True, height=TILE_H, chart_data=spark, chart_type="line",
          help="Median sale price across listed models.")
c3.metric("On discount", f"{len(disc_now)}",
          delta=delta_or_none(len(disc_now), len(disc_prev) if disc_prev is not None else None),
          border=True, height=TILE_H,
          help=(f"{len(disc_now) / n_now * 100:.0f}% of listed models have a sale price "
                "below list.") if n_now else None)
deepest = latest.loc[latest["discount_pct"].idxmax()] if latest["discount_pct"].notna().any() else None
c4.metric("Deepest discount",
          f"{deepest['discount_pct']:.0f}%" if deepest is not None else "n/a",
          border=True, height=TILE_H,
          help=str(deepest["title"]) if deepest is not None else None)

# Second tile row. Price spread is always available; the rating tiles fall back
# to "n/a" on any snapshot/filter where the enrich pass never got through, the
# same way an empty chart is avoided elsewhere.
has_price = latest["sale_price"].notna().any()
lo_row = latest.loc[latest["sale_price"].idxmin()] if has_price else None
hi_row = latest.loc[latest["sale_price"].idxmax()] if has_price else None
rated_now = latest.dropna(subset=["rating"])
rated_prev = prev.dropna(subset=["rating"]) if prev is not None else None
avg_rating = rated_now["rating"].mean() if not rated_now.empty else None
avg_rating_prev = (rated_prev["rating"].mean()
                   if rated_prev is not None and not rated_prev.empty else None)
reviews_now = rated_now["review_count"].sum() if not rated_now.empty else None

d1, d2, d3, d4 = st.columns(4)
d1.metric("Price range",
          f"{money(lo_row['sale_price'])} – {money(hi_row['sale_price'])}"
          if lo_row is not None else "n/a",
          border=True, height=TILE_H,
          help=(f"Cheapest: {lo_row['title']}\n\nPriciest: {hi_row['title']}")
          if lo_row is not None else None)
_rating_help = ("No ratings collected yet — run the local enrich pass."
                if avg_rating is None else
                f"Mean across {len(rated_now)} rated models"
                + (f", last observed {pd.Timestamp(rating_as_of).date()}."
                   if rating_stale else "."))
d2.metric("Average rating",
          f"{avg_rating:.2f} / 5" if avg_rating is not None else "n/a",
          delta=(f"{avg_rating - avg_rating_prev:+.2f}"
                 if delta_or_none(avg_rating, avg_rating_prev) else None),
          border=True, height=TILE_H, help=_rating_help)
d3.metric("Models rated", f"{len(rated_now)} / {n_now}" if n_now else "n/a",
          border=True, height=TILE_H,
          help="Listed models with a star rating (carried forward from the last "
               "enrich run on price-only days).")
d4.metric("Total reviews", f"{int(reviews_now):,}" if reviews_now else "n/a",
          border=True, height=TILE_H,
          help="Sum of review counts across rated models — a rough traction proxy.")

tab_price, tab_trend, tab_ratings, tab_catalog, tab_health = st.tabs(
    ["Price & discounts", "Trends", "Ratings & reviews", "Catalog changes", "Data health"]
)

# ================================================== price & discounts tab ===
with tab_price:
    if prev_date is None:
        st.info("Only one snapshot so far — price movement needs at least two. "
                "Run `python run_daily.py` again tomorrow.")
    else:
        st.subheader("Price movement")
        basis = st.radio("Compare against", ["Previous snapshot", "First tracked"],
                         horizontal=True, label_visibility="collapsed")
        if basis == "First tracked":
            first_seen = filtered.sort_values("snapshot_date").groupby("pid").head(1)
            old_p = first_seen.set_index("pid")["sale_price"]
            base_label = f"first snapshot ({pd.Timestamp(dates[0]).date()})"
        else:
            old_p = prev.set_index("pid")["sale_price"]
            base_label = str(pd.Timestamp(prev_date).date())

        cur_p = latest.set_index("pid")["sale_price"]
        common = cur_p.index.intersection(old_p.index)
        delta = (cur_p.loc[common] - old_p.loc[common]).dropna()
        movers = delta[delta != 0].sort_values()
        capped = len(movers) > 20
        if capped:
            keep = movers.abs().sort_values().tail(20).index
            movers = movers.loc[keep].sort_values()

        st.caption(f"Change in sale price, {base_label} → "
                   f"{pd.Timestamp(latest_date).date()}. Blue = price cut, red = price rise."
                   + (" Showing the 20 largest moves." if capped else ""))

        if movers.empty:
            st.success(f"No price changes between the last two snapshots — "
                       f"all {len(common)} models held their price.")
        else:
            titles = latest.set_index("pid")["title"]
            mv = pd.DataFrame({
                "pid": movers.index,
                "title": [titles.get(p, p) for p in movers.index],
                "change": movers.values,
                "from": [old_p.get(p) for p in movers.index],
                "to": [cur_p.get(p) for p in movers.index],
            })
            mv["short"] = short_titles(mv["title"])

            fig = go.Figure()
            fig.add_bar(
                x=mv["change"], y=hbar_categories(fig, mv["short"]), orientation="h",
                marker=dict(color=[P["down"] if v < 0 else P["up"] for v in mv["change"]],
                            line=dict(width=0)),
                text=[f"£{v:+,.0f}" for v in mv["change"]], textposition="outside",
                textfont=dict(color=P["text_secondary"], size=12, family=FONT),
                customdata=mv[["short", "from", "to"]].to_numpy(),
                hovertemplate="<b>%{customdata[0]}</b><br>£%{customdata[1]:,.0f} → "
                              "£%{customdata[2]:,.0f}<br>%{x:+,.0f}<extra></extra>",
                cliponaxis=False,
            )
            fig.add_vline(x=0, line_width=1, line_color=P["axis"])
            style_fig(fig, height=max(190, 34 * len(mv) + 60), xgrid=True, ygrid=False)
            fig.update_xaxes(title_text="Change in sale price (£)")
            fig.update_layout(margin=dict(l=4, r=64, t=4, b=4), bargap=0.45)
            st.plotly_chart(fig, width="stretch", theme=None)

            with st.expander(f"Table view — {len(mv)} models moved"):
                st.dataframe(
                    mv[["title", "from", "to", "change"]], hide_index=True, width="stretch",
                    column_config={
                        "title": "Model",
                        "from": st.column_config.NumberColumn("Was", format="£%.0f"),
                        "to": st.column_config.NumberColumn("Now", format="£%.0f"),
                        "change": st.column_config.NumberColumn("Change", format="£%+.0f"),
                    },
                )

    st.divider()
    left, right = st.columns(2)

    with left:
        st.subheader("Discount depth")
        order = ["None", "1–10%", "10–20%", "20–30%", "30%+"]
        bands = (pd.cut(latest["discount_pct"].fillna(0),
                        bins=[-0.01, 0.01, 10, 20, 30, 100], labels=order)
                 .value_counts().reindex(order).fillna(0))
        fig2 = go.Figure(go.Bar(
            x=list(bands.index), y=bands.values,
            marker=dict(color=P["series"][0], line=dict(width=0)),
            text=[int(v) if v else "" for v in bands.values], textposition="outside",
            textfont=dict(color=P["text_secondary"], size=12, family=FONT),
            hovertemplate="<b>%{x}</b><br>%{y} models<extra></extra>", cliponaxis=False,
        ))
        style_fig(fig2, height=300)
        fig2.update_yaxes(title_text="Models")
        fig2.update_layout(bargap=0.4)
        st.plotly_chart(fig2, width="stretch", theme=None)

    with right:
        st.subheader("Median price by category")
        bycat = (latest.groupby("category_path")
                 .agg(median_price=("sale_price", "median"), models=("pid", "nunique"))
                 .sort_values("median_price"))
        fig3 = go.Figure(go.Bar(
            x=bycat["median_price"], y=bycat.index, orientation="h",
            marker=dict(color=P["series"][0], line=dict(width=0)),
            text=[f"£{v:,.0f}" for v in bycat["median_price"]], textposition="outside",
            textfont=dict(color=P["text_secondary"], size=12, family=FONT),
            customdata=bycat[["models"]].to_numpy(),
            hovertemplate="<b>%{y}</b><br>median £%{x:,.0f}<br>"
                          "%{customdata[0]} models<extra></extra>",
            cliponaxis=False,
        ))
        style_fig(fig3, height=300, xgrid=True, ygrid=False)
        fig3.update_layout(margin=dict(l=4, r=64, t=4, b=4), bargap=0.4)
        st.plotly_chart(fig3, width="stretch", theme=None)

    st.divider()
    st.subheader("Most and least expensive")
    ranked = latest.dropna(subset=["sale_price"]).copy()
    ranked["short"] = short_titles(ranked["title"])
    # Collapse frame-size variants ("Apollo Phaze-E ... - L Frame" / "- M, L
    # Frames") to one row so three near-identical SKUs don't fill the top ten.
    ranked = ranked.sort_values("sale_price").drop_duplicates("short")
    ends = pd.concat([ranked.head(10), ranked.tail(10)]).drop_duplicates("pid")
    if len(ends) < 2:
        st.caption("Not enough priced models to rank.")
    else:
        med = latest["sale_price"].median()
        st.caption(f"Ten cheapest and ten priciest listed models. Dotted line is "
                   f"the median (£{med:,.0f}); the mid-range is left out.")
        fig6 = go.Figure()
        fig6.add_bar(
            x=ends["sale_price"], y=hbar_categories(fig6, ends["short"]), orientation="h",
            marker=dict(color=P["series"][0], line=dict(width=0)),
            text=[f"£{v:,.0f}" for v in ends["sale_price"]], textposition="outside",
            textfont=dict(color=P["text_secondary"], size=12, family=FONT),
            customdata=ends[["short", "category_path"]].to_numpy(),
            hovertemplate="<b>%{customdata[0]}</b><br>£%{x:,.0f}<br>"
                          "%{customdata[1]}<extra></extra>",
            cliponaxis=False,
        )
        fig6.add_vline(x=med, line_width=1, line_dash="dot", line_color=P["axis"])
        style_fig(fig6, height=max(300, 26 * len(ends) + 60), xgrid=True, ygrid=False)
        fig6.update_xaxes(title_text="Sale price (£)")
        fig6.update_layout(margin=dict(l=4, r=64, t=4, b=4), bargap=0.35)
        st.plotly_chart(fig6, width="stretch", theme=None)

# ================================================================ trends ====
with tab_trend:
    if len(dates) < 2:
        st.info("Trends need at least two snapshots. Run `python run_daily.py` again tomorrow.")
    else:
        st.subheader("Price over time")
        opts = sorted(filtered["title"].unique())
        moved = filtered.groupby("pid")["sale_price"].nunique()
        moved_pids = moved[moved > 1].index
        default = filtered[filtered["pid"].isin(moved_pids)]["title"].drop_duplicates().tolist()[:5]
        sel = st.multiselect("Models (max 8 — colours are assigned in fixed order)",
                             opts, default=default or opts[:5], max_selections=8)
        trend = filtered[filtered["title"].isin(sel)].sort_values("snapshot_date")

        if trend.empty:
            st.caption("Pick at least one model above.")
        else:
            fig4 = go.Figure()
            for i, (title, grp) in enumerate(trend.groupby("title", sort=False)):
                colour = P["series"][i % len(P["series"])]
                grp = grp.sort_values("snapshot_date")
                # Full titles ("... - M, L Frames") wrap the legend onto several
                # rows and it then overlaps the top series. Trim to the model name.
                short = title.split(" - ")[0][:34]
                fig4.add_trace(go.Scatter(
                    x=grp["snapshot_date"], y=grp["sale_price"], mode="lines+markers",
                    name=short, line=dict(color=colour, width=2),
                    marker=dict(color=colour, size=8, line=dict(color=P["surface"], width=2)),
                    hovertemplate=f"<b>{title}</b><br>%{{x|%d %b}}<br>"
                                  "£%{y:,.0f}<extra></extra>",
                ))
                # Direct-label the endpoint only - never a number on every point.
                last = grp.iloc[-1]
                fig4.add_trace(go.Scatter(
                    x=[last["snapshot_date"]], y=[last["sale_price"]], mode="text",
                    text=[f"  £{last['sale_price']:,.0f}"], textposition="middle right",
                    textfont=dict(color=P["text_secondary"], size=12, family=FONT),
                    showlegend=False, hoverinfo="skip", cliponaxis=False,
                ))
            style_fig(fig4, height=400)
            # Reserve a header band for the legend - at t=4 it sits on top of
            # the highest series.
            fig4.update_layout(showlegend=True, margin=dict(l=4, r=76, t=56, b=4))
            fig4.update_yaxes(title_text="Sale price (£)")
            st.plotly_chart(fig4, width="stretch", theme=None)

            with st.expander("Table view"):
                pivot = trend.pivot_table(index="snapshot_date", columns="title",
                                          values="sale_price", aggfunc="last")
                pivot.index = pivot.index.date
                st.dataframe(pivot, width="stretch")

            rated = trend.dropna(subset=["rating"])
            if not rated.empty:
                st.divider()
                st.subheader("Rating over time")
                fig5 = go.Figure()
                for i, (title, grp) in enumerate(rated.groupby("title", sort=False)):
                    colour = P["series"][i % len(P["series"])]
                    fig5.add_trace(go.Scatter(
                        x=grp["snapshot_date"], y=grp["rating"], mode="lines+markers",
                        name=title.split(" - ")[0][:34], line=dict(color=colour, width=2),
                        marker=dict(color=colour, size=8,
                                    line=dict(color=P["surface"], width=2)),
                    ))
                style_fig(fig5, height=320)
                fig5.update_layout(showlegend=True, margin=dict(l=4, r=4, t=56, b=4))
                fig5.update_yaxes(title_text="Rating (/5)", range=[0, 5])
                st.plotly_chart(fig5, width="stretch", theme=None)

# ===================================================== ratings & reviews ====
with tab_ratings:
    rated = latest.dropna(subset=["rating"]).copy()
    if rated.empty:
        st.info(
            "No ratings collected yet. Rating, review counts and badges ride on "
            "the local enrich pass (`python run_daily.py`), which is best-effort "
            "against Halfords' bot protection — the daily cloud refresh is "
            "price-only. See the Data health tab. Re-run it locally after a "
            "cooldown, then commit the DB."
        )
    else:
        rated["review_count"] = rated["review_count"].fillna(0).astype(int)
        rated["short"] = short_titles(rated["title"])
        n_rated = len(rated)
        stamp = pd.Timestamp(rating_as_of or latest_date).date()
        st.caption(
            f"{n_rated} of {int(latest['pid'].nunique())} listed models carry a rating"
            + (f", last observed {stamp} — carried forward on price-only days since. "
               "Re-run the local enrich pass to refresh." if rating_stale
               else f", as observed on {stamp}.")
        )

        MIN_REVIEWS = 3
        st.subheader("Best rated")
        st.caption(f"Highest star rating among models with at least {MIN_REVIEWS} "
                   "reviews, so a lone 5-star review can't top the list.")
        top = (rated[rated["review_count"] >= MIN_REVIEWS]
               .sort_values(["rating", "review_count"]).tail(12))
        if top.empty:
            st.caption("No models clear the review threshold yet.")
        else:
            fig = go.Figure()
            fig.add_bar(
                x=top["rating"], y=hbar_categories(fig, top["short"]), orientation="h",
                marker=dict(color=P["series"][0], line=dict(width=0)),
                text=[f"{v:.1f}" for v in top["rating"]], textposition="outside",
                textfont=dict(color=P["text_secondary"], size=12, family=FONT),
                customdata=top[["short", "review_count", "sale_price"]].to_numpy(),
                hovertemplate="<b>%{customdata[0]}</b><br>%{x:.1f} / 5 · "
                              "%{customdata[1]} reviews<br>£%{customdata[2]:,.0f}<extra></extra>",
                cliponaxis=False,
            )
            style_fig(fig, height=max(200, 30 * len(top) + 60), xgrid=True, ygrid=False)
            fig.update_xaxes(title_text="Rating (/5)", range=[0, 5])
            fig.update_layout(margin=dict(l=4, r=44, t=4, b=4), bargap=0.4)
            st.plotly_chart(fig, width="stretch", theme=None)

        st.divider()
        left, right = st.columns(2)
        with left:
            st.subheader("Most reviewed")
            st.caption("Review volume is the nearest thing here to a sales signal.")
            mr = rated[rated["review_count"] > 0].sort_values("review_count").tail(12)
            if mr.empty:
                st.caption("No review counts yet.")
            else:
                figm = go.Figure()
                figm.add_bar(
                    x=mr["review_count"], y=hbar_categories(figm, mr["short"]),
                    orientation="h",
                    marker=dict(color=P["series"][0], line=dict(width=0)),
                    text=[f"{int(v)}" for v in mr["review_count"]], textposition="outside",
                    textfont=dict(color=P["text_secondary"], size=12, family=FONT),
                    customdata=mr[["short", "rating"]].to_numpy(),
                    hovertemplate="<b>%{customdata[0]}</b><br>%{x} reviews · "
                                  "%{customdata[1]:.1f} / 5<extra></extra>",
                    cliponaxis=False,
                )
                style_fig(figm, height=max(200, 30 * len(mr) + 60), xgrid=True, ygrid=False)
                figm.update_xaxes(title_text="Reviews")
                figm.update_layout(margin=dict(l=4, r=44, t=4, b=4), bargap=0.4)
                st.plotly_chart(figm, width="stretch", theme=None)

        with right:
            st.subheader("Rating distribution")
            order = ["< 3", "3–3.5", "3.5–4", "4–4.5", "4.5–5"]
            bands = (pd.cut(rated["rating"], bins=[0, 3, 3.5, 4, 4.5, 5.01],
                            labels=order, right=False)
                     .value_counts().reindex(order).fillna(0))
            figd = go.Figure(go.Bar(
                x=order, y=bands.values,
                marker=dict(color=P["series"][0], line=dict(width=0)),
                text=[int(v) if v else "" for v in bands.values], textposition="outside",
                textfont=dict(color=P["text_secondary"], size=12, family=FONT),
                hovertemplate="<b>%{x}</b><br>%{y} models<extra></extra>", cliponaxis=False,
            ))
            style_fig(figd, height=300)
            figd.update_yaxes(title_text="Models")
            figd.update_layout(bargap=0.4)
            st.plotly_chart(figd, width="stretch", theme=None)

        st.divider()
        st.subheader("Rating vs price")
        st.caption("Bottom-right is the sweet spot — well rated and cheap. "
                   "Marker size is review volume.")
        sizes = rated["review_count"].clip(lower=1)
        figv = go.Figure(go.Scatter(
            x=rated["sale_price"], y=rated["rating"], mode="markers",
            marker=dict(color=P["series"][0], size=sizes, sizemode="area",
                        sizeref=2.0 * sizes.max() / (38 ** 2), sizemin=4,
                        line=dict(color=P["surface"], width=1)),
            customdata=rated[["title", "review_count"]].to_numpy(),
            hovertemplate="<b>%{customdata[0]}</b><br>£%{x:,.0f} · %{y:.1f} / 5"
                          "<br>%{customdata[1]} reviews<extra></extra>",
        ))
        style_fig(figv, height=380, xgrid=True, ygrid=True)
        figv.update_xaxes(title_text="Sale price (£)")
        figv.update_yaxes(title_text="Rating (/5)",
                          range=[max(0, rated["rating"].min() - 0.3), 5.1])
        st.plotly_chart(figv, width="stretch", theme=None)

        with st.expander(f"Table view — {n_rated} rated models"):
            tv = rated[["title", "category_path", "sale_price", "rating",
                        "review_count"]].sort_values("rating", ascending=False)
            st.dataframe(
                tv, hide_index=True, width="stretch",
                column_config={
                    "title": "Model", "category_path": "Category",
                    "sale_price": st.column_config.NumberColumn("Price", format="£%.0f"),
                    "rating": st.column_config.NumberColumn("Rating", format="%.1f"),
                    "review_count": st.column_config.NumberColumn("Reviews"),
                },
            )

# ======================================================= catalog changes ====
with tab_catalog:
    if prev_date is None:
        st.info("Catalog changes need at least two snapshots.")
    else:
        prev_pids, curr_pids = set(prev["pid"]), set(latest["pid"])
        new_pids, gone_pids = curr_pids - prev_pids, prev_pids - curr_pids
        new_rows = latest[latest["pid"].isin(new_pids)]
        gone_rows = prev[prev["pid"].isin(gone_pids)]

        a, b = st.columns(2)
        a.metric("New since last snapshot", len(new_pids), border=True, height=TILE_H)
        b.metric("No longer listed", len(gone_pids), border=True, height=TILE_H,
                 delta=-len(gone_pids) if gone_pids else None)

        cfg = {
            "title": "Model", "category_path": "Category",
            "sale_price": st.column_config.NumberColumn("Price", format="£%.0f"),
            "url": st.column_config.LinkColumn("Link", display_text="open"),
        }
        cols = ["title", "category_path", "sale_price", "url"]

        st.subheader(f"New since {pd.Timestamp(prev_date).date()}")
        if new_rows.empty:
            st.caption("Nothing new.")
        else:
            st.dataframe(new_rows[cols], hide_index=True, width="stretch", column_config=cfg)

        st.subheader(f"Dropped as of {pd.Timestamp(latest_date).date()}")
        if gone_rows.empty:
            st.caption("Nothing dropped.")
        else:
            st.caption("Present in the previous snapshot, absent from the latest. "
                       "A delisting — or a title/URL change that minted a new product id.")
            st.dataframe(gone_rows[cols], hide_index=True, width="stretch", column_config=cfg)

    st.divider()
    st.subheader(f"Full listing — {pd.Timestamp(latest_date).date()}")
    if latest["merch_badges"].isna().all():
        st.warning(
            "**Badges and tile ratings not collected yet** — the Badges column is all `—` "
            "(not observed). The listing pass either hasn't run since the fix or was "
            "refused: Halfords serves a degraded page (200 OK, but badge/rating "
            "components never hydrate) to a flagged client, and the scraper rejects "
            "that rather than recording it as \"no badges\". Re-run "
            "`python run_daily.py` after a cooldown.",
            icon=":material/info:",
        )
    show = latest.sort_values("discount_pct", ascending=False, na_position="last").copy()
    show["badges"] = show["merch_badges"].map(
        lambda v: "—" if v is None or (isinstance(v, float) and pd.isna(v))
        else (v.replace(",", " · ") if v else "none")
    )
    st.dataframe(
        show[["title", "category_path", "price", "sale_price", "discount_pct",
              "badges", "in_stock", "rating", "url"]],
        hide_index=True, width="stretch",
        column_config={
            "title": "Model", "category_path": "Category",
            "price": st.column_config.NumberColumn("List", format="£%.0f"),
            "sale_price": st.column_config.NumberColumn("Sale", format="£%.0f"),
            "discount_pct": st.column_config.NumberColumn("Disc.", format="%.0f%%"),
            "badges": st.column_config.TextColumn(
                "Badges", help="'—' = not observed this run; 'none' = observed, no badge"),
            "in_stock": st.column_config.CheckboxColumn("In stock"),
            "rating": st.column_config.NumberColumn("Rating", format="%.1f"),
            "url": st.column_config.LinkColumn("Link", display_text="open"),
        },
    )

# =========================================================== data health ====
with tab_health:
    st.subheader("Field coverage by snapshot")
    st.caption("What each run actually captured. Price and catalog come from the "
               "Bloomreach search API and are reliable; stock, rating and badges "
               "need a browser past Akamai and routinely come back empty.")

    fields = {"price": "Price", "sale_price": "Sale price", "merch_badges": "Merch badges",
              "in_stock": "Stock", "rating": "Rating", "review_count": "Reviews"}
    rows = []
    for date in dates:
        snap = filtered[filtered["snapshot_date"] == date]
        row = {"Snapshot": str(pd.Timestamp(date).date()), "Rows": len(snap)}
        for col, label in fields.items():
            n = int(snap[col].notna().sum())
            pct = n / len(snap) * 100 if len(snap) else 0
            mark = "✓ full" if pct >= 99 else ("◐ partial" if pct > 0 else "✕ none")
            row[label] = f"{mark} · {n}/{len(snap)}"
        rows.append(row)
    st.dataframe(pd.DataFrame(rows).iloc[::-1], hide_index=True, width="stretch")

    st.info(
        "**`merch_badges` is tri-state** — don't read a blank as \"no badges\":\n\n"
        "- `—` / NULL — not observed (pass skipped, blocked, or the read was rejected)\n"
        "- `none` — observed, the product genuinely carries no badge\n"
        "- `Sale · TopRated` — observed badges\n\n"
        "Collapsing the first two would turn every blocked run into a fake "
        "\"all badges disappeared\" event."
    )

    if not runs.empty:
        st.subheader("Recent runs")
        r = runs.copy()
        r["started_at"] = pd.to_datetime(r["started_at"], format="mixed", utc=True
                                         ).dt.strftime("%Y-%m-%d %H:%M")
        r["status"] = r["error"].map(lambda e: "✕ error" if isinstance(e, str) and e else "✓ ok")
        st.dataframe(
            r[["started_at", "distributor", "catalog_count", "enriched_count", "status", "error"]],
            hide_index=True, width="stretch",
            column_config={
                "started_at": "Started (UTC)", "distributor": "Distributor",
                "catalog_count": st.column_config.NumberColumn("Catalog"),
                "enriched_count": st.column_config.NumberColumn("Enriched"),
                "status": "Status", "error": "Error",
            },
        )

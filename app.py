import io
import re

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Groundwater Ionic Balance Calculator",
    page_icon="💧",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Equivalent weights (mg per meq) = molar mass / |charge|
# ---------------------------------------------------------------------------
CATIONS = {
    "Ca": 20.039,
    "Mg": 12.153,
    "Na": 22.990,
    "K": 39.098,
    "NH4": 18.039,
    "Fe": 27.923,
    "Mn": 27.469,
}
ANIONS = {
    "HCO3": 61.017,
    "CO3": 30.005,
    "Cl": 35.453,
    "SO4": 48.030,
    "NO3": 62.004,
    "F": 18.998,
}
ALL_IONS = {**CATIONS, **ANIONS}
MAJOR_IONS = ["Ca", "Mg", "Na", "HCO3", "Cl", "SO4"]

ALIASES = {
    "Ca": ["ca", "ca2", "calcium"],
    "Mg": ["mg", "mg2", "magnesium"],
    "Na": ["na", "sodium"],
    "K": ["k", "potassium"],
    "NH4": ["nh4", "ammonium"],
    "Fe": ["fe", "fe2", "iron"],
    "Mn": ["mn", "mn2", "manganese"],
    "HCO3": ["hco3", "bicarbonate"],
    "CO3": ["co3", "co32", "carbonate"],
    "Cl": ["cl", "chloride"],
    "SO4": ["so4", "so42", "sulfate", "sulphate"],
    "NO3": ["no3", "nitrate"],
    "F": ["f", "fluoride"],
}
LOOKUP = {alias: ion for ion, names in ALIASES.items() for alias in names}
ID_ALIASES = {
    "sample", "sampleid", "sampleno", "id", "name", "well",
    "station", "location", "sno", "slno",
}


def clean(name) -> str:
    """Normalise a column header: 'Ca2+ (mg/L)' -> 'ca2', 'SO4 2-' -> 'so42'."""
    s = str(name).lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    s = re.sub(r"(mgperl|mgl|meql|ppm)$", "", s)
    return s


def status_label(cbe: float, limit: float) -> str:
    if pd.isna(cbe):
        return "No data"
    a = abs(cbe)
    if a <= limit:
        return "Acceptable"
    if a <= 2 * limit:
        return "Marginal"
    return "Check data"


def calculate(df: pd.DataFrame, unit: str, limit: float, selected=None):
    """Return (results_df, meq_df, ions_used, ions_excluded)."""
    df = df.reset_index(drop=True)

    id_col = next((c for c in df.columns if clean(c) in ID_ALIASES), None)
    if id_col is not None:
        ids = df[id_col].astype(str)
    else:
        ids = pd.Series(np.arange(1, len(df) + 1)).astype(str)

    # Map user columns to known ions
    found = {}
    for col in df.columns:
        ion = LOOKUP.get(clean(col))
        if ion and ion not in found:
            found[ion] = col

    # Apply the user's include/exclude choices
    excluded = []
    if selected is not None:
        excluded = [i for i in found if i not in selected]
        found = {i: c for i, c in found.items() if i in selected}

    data = pd.DataFrame(index=df.index)
    for ion, col in found.items():
        data[ion] = pd.to_numeric(df[col], errors="coerce")

    if data.empty:
        return None, None, [], excluded

    # Drop rows with no ion data at all; blanks inside a row count as zero
    keep = data.notna().any(axis=1)
    data, ids = data[keep], ids[keep]
    data = data.fillna(0.0)

    meq = pd.DataFrame(index=data.index)
    for ion in data.columns:
        meq[ion] = data[ion] if unit == "meq/L" else data[ion] / ALL_IONS[ion]

    cat_cols = [i for i in meq.columns if i in CATIONS]
    an_cols = [i for i in meq.columns if i in ANIONS]
    sum_cat = meq[cat_cols].sum(axis=1)
    sum_an = meq[an_cols].sum(axis=1)
    total = sum_cat + sum_an
    cbe = ((sum_cat - sum_an) / total * 100).where(total > 0)

    res = pd.DataFrame(
        {
            "Sample": ids,
            "Sum cations (meq/L)": sum_cat,
            "Sum anions (meq/L)": sum_an,
            "CBE (%)": cbe,
        }
    )
    res["Status"] = [status_label(v, limit) for v in res["CBE (%)"]]
    res = res.reset_index(drop=True)

    meq_out = meq.copy()
    meq_out.insert(0, "Sample", ids)
    meq_out = meq_out.reset_index(drop=True)
    return res, meq_out, list(found.keys()), excluded


def to_excel_bytes(res: pd.DataFrame, meq: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        res.to_excel(writer, index=False, sheet_name="Ionic balance")
        meq.to_excel(writer, index=False, sheet_name="Ions in meq per L")
    return buf.getvalue()


def colour_status(v):
    return {
        "Acceptable": "background-color:#c8e6c9;color:#1b5e20",
        "Marginal": "background-color:#fff3cd;color:#7a5c00",
        "Check data": "background-color:#f8d7da;color:#842029",
    }.get(v, "")


def show_results(df: pd.DataFrame, unit: str, limit: float, selected: set, key: str):
    res, meq, found, excluded = calculate(df, unit, limit, selected)
    if res is None or res.empty:
        st.warning(
            "No ion columns to calculate with. Either no headers were recognised "
            "(use Ca, Mg, Na, K, HCO3, CO3, Cl, SO4, NO3, F) or every ion was "
            "excluded in the sidebar."
        )
        return

    missing = [i for i in MAJOR_IONS if i not in found and i not in excluded]
    if missing:
        st.warning(
            "Missing major ion column(s): **" + ", ".join(missing) + "**. "
            "They are treated as zero, which will distort the balance."
        )

    st.caption("Ions used: " + ", ".join(found))
    if excluded:
        st.caption("Excluded by you: " + ", ".join(excluded))

    n_ok = int((res["Status"] == "Acceptable").sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Samples", len(res))
    c2.metric(f"Within ±{limit:g}%", n_ok)
    c3.metric("Need checking", len(res) - n_ok)

    styled = res.style.map(colour_status, subset=["Status"]).format(
        {
            "Sum cations (meq/L)": "{:.3f}",
            "Sum anions (meq/L)": "{:.3f}",
            "CBE (%)": "{:+.2f}",
        }
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

    st.bar_chart(res.set_index("Sample")["CBE (%)"])

    with st.expander("Show each ion in meq/L"):
        st.dataframe(meq.round(4), use_container_width=True, hide_index=True)

    d1, d2 = st.columns(2)
    d1.download_button(
        "⬇️ Download results (CSV)",
        res.to_csv(index=False).encode("utf-8"),
        "ionic_balance_results.csv",
        "text/csv",
        key=f"csv_{key}",
    )
    d2.download_button(
        "⬇️ Download results (Excel)",
        to_excel_bytes(res, meq),
        "ionic_balance_results.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"xlsx_{key}",
    )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    unit = st.radio("Concentration unit in your data", ["mg/L", "meq/L"])
    limit = st.slider("Acceptable CBE limit (±%)", 1, 10, 5)
    st.subheader("Ions to include")
    st.caption("Untick an ion to leave it out of the balance.")
    selected = set()
    col_c, col_a = st.columns(2)
    with col_c:
        st.markdown("**Cations**")
        for ion in CATIONS:
            if st.checkbox(ion, value=True, key=f"inc_{ion}"):
                selected.add(ion)
    with col_a:
        st.markdown("**Anions**")
        for ion in ANIONS:
            if st.checkbox(ion, value=True, key=f"inc_{ion}"):
                selected.add(ion)
    st.divider()
    st.markdown(
        """
**Charge balance error**

CBE % = (Σcations − Σanions) / (Σcations + Σanions) × 100

Σ in meq/L. For mg/L input, each ion is divided by its equivalent weight.

- Acceptable: within ±limit
- Marginal: up to 2× the limit
- Check data: beyond that
"""
    )

# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------
st.title("💧 Groundwater Ionic Balance Calculator")
st.write(
    "Upload your water-quality data or type it in. The app converts to meq/L "
    "and computes the charge balance error for every sample."
)

template = pd.DataFrame(
    {
        "Sample": ["GW-1", "GW-2"],
        "Ca": [62.0, 88.0],
        "Mg": [18.0, 30.0],
        "Na": [45.0, 120.0],
        "K": [4.0, 8.0],
        "HCO3": [245.0, 310.0],
        "CO3": [0.0, 0.0],
        "Cl": [38.0, 150.0],
        "SO4": [41.0, 95.0],
        "NO3": [12.0, 20.0],
        "F": [0.6, 1.1],
    }
)

tab_upload, tab_manual = st.tabs(["📁 Upload file", "⌨️ Type / paste data"])

with tab_upload:
    st.download_button(
        "Download CSV template",
        template.to_csv(index=False).encode("utf-8"),
        "ionic_balance_template.csv",
        "text/csv",
    )
    up = st.file_uploader("CSV or Excel file", type=["csv", "xlsx", "xls"])
    if up is not None:
        try:
            raw = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up)
            st.write(f"Loaded **{len(raw)}** rows.")
            show_results(raw, unit, limit, selected, key="upload")
        except Exception as e:
            st.error(f"Could not read the file: {e}")

with tab_manual:
    st.write("Add rows with the ➕ at the bottom of the table, or paste straight from Excel.")
    ion_cols = [c for c in template.columns if c != "Sample"]
    blank = pd.DataFrame(
        {"Sample": [""] * 5, **{c: [np.nan] * 5 for c in ion_cols}}
    )
    edited = st.data_editor(blank, num_rows="dynamic", use_container_width=True, key="editor")
    if edited[ion_cols].notna().any().any():
        show_results(edited, unit, limit, selected, key="manual")
    else:
        st.info("Enter at least one sample to see results.")

st.divider()
st.caption(
    "Tip: a CBE beyond about ±5–10% usually points to an analytical error, a missing "
    "ion (e.g. Fe, NH4, organic anions) or a typing mistake. Always review flagged samples."
)

from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st


ROOT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = ROOT_DIR / "data" / "gold" / "gold.duckdb"
SILVER_PATH = ROOT_DIR / "data" / "silver" / "questions_enriched.parquet"
REQUIRED_COLUMNS = {
    "category",
    "type",
    "difficulty",
    "question_clean",
    "correct_answer_clean",
    "model",
    "ai_answer",
    "ai_correct",
    "response_time",
    "status",
    "error",
}
DIFFICULTY_ORDER = ["easy", "medium", "hard"]


st.set_page_config(
    page_title="Benchmark LLM · OpenTDB",
    page_icon="🧠",
    layout="wide",
)


@st.cache_data(show_spinner="Chargement des résultats…")
def load_benchmark(database_mtime: int, silver_mtime: int) -> pd.DataFrame:
    """Load the dbt staging view; mtimes invalidate Streamlit's cache."""
    del database_mtime, silver_mtime
    connection = duckdb.connect(str(DATABASE_PATH), read_only=True)
    try:
        return connection.execute("SELECT * FROM stg_questions_enriched").fetchdf()
    finally:
        connection.close()


def format_duration(seconds: float) -> str:
    if pd.isna(seconds):
        return "—"
    return f"{seconds:.3f} s"


def aggregate_results(data: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    grouped = (
        data.groupby(dimensions, observed=True, dropna=False)
        .agg(
            questions=("ai_correct", "size"),
            bonnes_reponses=("ai_correct", "sum"),
            precision=("ai_correct", "mean"),
            latence_moyenne=("response_time", "mean"),
            latence_mediane=("response_time", "median"),
        )
        .reset_index()
    )
    grouped["precision_pct"] = grouped["precision"] * 100
    return grouped


def stop_with_setup_help(message: str) -> None:
    st.error(message)
    st.code(
        ".\\.venv\\Scripts\\dbt.exe run --profiles-dir .\n"
        ".\\.venv\\Scripts\\streamlit.exe run streamlit_app.py",
        language="powershell",
    )
    st.stop()


st.title("Benchmark LLM sur Open Trivia Database")
st.caption(
    "Analyse de la précision, de la rapidité et des erreurs du modèle sur les questions OpenTDB."
)

if not DATABASE_PATH.exists():
    stop_with_setup_help(
        f"Base DuckDB introuvable : `{DATABASE_PATH.relative_to(ROOT_DIR)}`. "
        "Construisez d'abord la couche Gold avec dbt."
    )

try:
    database_mtime = DATABASE_PATH.stat().st_mtime_ns
    silver_mtime = SILVER_PATH.stat().st_mtime_ns if SILVER_PATH.exists() else 0
    benchmark = load_benchmark(database_mtime, silver_mtime)
except (duckdb.Error, OSError) as exc:
    stop_with_setup_help(f"Impossible de lire les résultats DuckDB : {exc}")

missing_columns = REQUIRED_COLUMNS.difference(benchmark.columns)
if missing_columns:
    stop_with_setup_help(
        "Le schéma du benchmark est incomplet. Colonnes manquantes : "
        + ", ".join(sorted(missing_columns))
    )

benchmark["ai_correct"] = benchmark["ai_correct"].astype("boolean")
benchmark["response_time"] = pd.to_numeric(benchmark["response_time"], errors="coerce")

with st.sidebar:
    st.header("Filtres")
    available_models = sorted(benchmark["model"].dropna().unique().tolist())
    available_categories = sorted(benchmark["category"].dropna().unique().tolist())
    available_difficulties = [
        difficulty
        for difficulty in DIFFICULTY_ORDER
        if difficulty in benchmark["difficulty"].dropna().unique()
    ]

    selected_models = st.multiselect("Modèles", available_models, default=available_models)
    selected_difficulties = st.multiselect(
        "Difficultés", available_difficulties, default=available_difficulties
    )
    selected_categories = st.multiselect(
        "Catégories", available_categories, default=available_categories
    )

    st.divider()
    st.caption(f"Source : `{DATABASE_PATH.relative_to(ROOT_DIR)}`")
    if st.button("Actualiser les données", width="stretch"):
        st.cache_data.clear()
        st.rerun()

filtered = benchmark[
    benchmark["model"].isin(selected_models)
    & benchmark["difficulty"].isin(selected_difficulties)
    & benchmark["category"].isin(selected_categories)
].copy()

if filtered.empty:
    st.warning("Aucune question ne correspond aux filtres sélectionnés.")
    st.stop()

successful = filtered[filtered["status"].eq("success")].copy()
failed = filtered[~filtered["status"].eq("success")].copy()

if successful.empty:
    st.warning("Aucun résultat exploitable ne correspond aux filtres sélectionnés.")
    st.stop()

accuracy = float(successful["ai_correct"].mean() * 100)
average_latency = float(successful["response_time"].mean())
median_latency = float(successful["response_time"].median())
p95_latency = float(successful["response_time"].quantile(0.95))

kpi_columns = st.columns(5)
kpi_columns[0].metric("Questions", f"{len(filtered):,}".replace(",", " "))
kpi_columns[1].metric("Taux de succès", f"{len(successful) / len(filtered) * 100:.2f} %")
kpi_columns[2].metric("Précision", f"{accuracy:.2f} %")
kpi_columns[3].metric("Latence moyenne", format_duration(average_latency))
kpi_columns[4].metric("Latence P95", format_duration(p95_latency))

overview_tab, categories_tab, latency_tab, comparison_tab, details_tab = st.tabs(
    ["Vue d'ensemble", "Catégories", "Latence", "Modèles", "Données et erreurs"]
)

with overview_tab:
    left, right = st.columns(2)

    difficulty_results = aggregate_results(successful, ["difficulty"])
    difficulty_results["difficulty"] = pd.Categorical(
        difficulty_results["difficulty"], categories=DIFFICULTY_ORDER, ordered=True
    )
    difficulty_results = difficulty_results.sort_values("difficulty")

    with left:
        st.subheader("Précision par difficulté")
        difficulty_accuracy = px.bar(
            difficulty_results,
            x="difficulty",
            y="precision_pct",
            text="precision_pct",
            labels={"difficulty": "Difficulté", "precision_pct": "Précision (%)"},
            color="precision_pct",
            color_continuous_scale="Blues",
            range_y=[0, 100],
        )
        difficulty_accuracy.update_traces(texttemplate="%{text:.1f} %", textposition="outside")
        difficulty_accuracy.update_layout(coloraxis_showscale=False)
        st.plotly_chart(difficulty_accuracy, width="stretch")

    with right:
        st.subheader("Volume de questions")
        question_volume = px.bar(
            difficulty_results,
            x="difficulty",
            y="questions",
            text="questions",
            labels={"difficulty": "Difficulté", "questions": "Questions"},
            color="difficulty",
            category_orders={"difficulty": DIFFICULTY_ORDER},
        )
        question_volume.update_layout(showlegend=False)
        st.plotly_chart(question_volume, width="stretch")

    st.subheader("Précision selon le type de question")
    type_results = aggregate_results(successful, ["type"])
    type_chart = px.bar(
        type_results,
        x="type",
        y="precision_pct",
        text="precision_pct",
        hover_data=["questions", "latence_moyenne"],
        labels={"type": "Type", "precision_pct": "Précision (%)"},
        range_y=[0, 100],
        color="type",
    )
    type_chart.update_traces(texttemplate="%{text:.1f} %", textposition="outside")
    type_chart.update_layout(showlegend=False)
    st.plotly_chart(type_chart, width="stretch")

with categories_tab:
    st.subheader("Performance par catégorie")
    category_results = aggregate_results(successful, ["category"]).sort_values(
        "precision_pct", ascending=True
    )
    category_chart = px.bar(
        category_results,
        x="precision_pct",
        y="category",
        orientation="h",
        hover_data=["questions", "bonnes_reponses", "latence_moyenne"],
        labels={"category": "Catégorie", "precision_pct": "Précision (%)"},
        color="precision_pct",
        color_continuous_scale="RdYlGn",
        range_x=[0, 100],
        height=max(500, len(category_results) * 27),
    )
    category_chart.update_layout(coloraxis_showscale=False)
    st.plotly_chart(category_chart, width="stretch")

    st.subheader("Catégories × difficulté")
    category_difficulty = aggregate_results(successful, ["category", "difficulty"])
    heatmap_data = category_difficulty.pivot(
        index="category", columns="difficulty", values="precision_pct"
    ).reindex(columns=DIFFICULTY_ORDER)
    heatmap = px.imshow(
        heatmap_data,
        text_auto=".1f",
        aspect="auto",
        color_continuous_scale="RdYlGn",
        zmin=0,
        zmax=100,
        labels={"x": "Difficulté", "y": "Catégorie", "color": "Précision (%)"},
        height=max(500, len(heatmap_data) * 27),
    )
    st.plotly_chart(heatmap, width="stretch")

with latency_tab:
    st.subheader("Distribution des temps de réponse")
    latency_histogram = px.histogram(
        successful,
        x="response_time",
        color="difficulty",
        category_orders={"difficulty": DIFFICULTY_ORDER},
        nbins=40,
        barmode="overlay",
        opacity=0.65,
        labels={"response_time": "Temps de réponse (s)", "count": "Questions"},
    )
    st.plotly_chart(latency_histogram, width="stretch")

    left, right = st.columns(2)
    with left:
        st.subheader("Latence par difficulté")
        latency_by_difficulty = px.bar(
            difficulty_results,
            x="difficulty",
            y=["latence_moyenne", "latence_mediane"],
            barmode="group",
            labels={"difficulty": "Difficulté", "value": "Secondes", "variable": "Mesure"},
        )
        st.plotly_chart(latency_by_difficulty, width="stretch")

    with right:
        st.subheader("Précision et rapidité par catégorie")
        speed_accuracy = px.scatter(
            category_results,
            x="latence_moyenne",
            y="precision_pct",
            size="questions",
            hover_name="category",
            labels={
                "latence_moyenne": "Latence moyenne (s)",
                "precision_pct": "Précision (%)",
                "questions": "Questions",
            },
            range_y=[0, 100],
        )
        st.plotly_chart(speed_accuracy, width="stretch")

    st.caption(
        f"Médiane globale : {format_duration(median_latency)} · "
        f"95 % des réponses arrivent en moins de {format_duration(p95_latency)}."
    )

with comparison_tab:
    st.subheader("Comparaison des modèles")
    model_results = aggregate_results(successful, ["model"]).sort_values(
        "precision_pct", ascending=False
    )
    if len(model_results) == 1:
        st.info(
            "Une seule exécution de modèle est présente. Le tableau est prêt à comparer "
            "plusieurs modèles dès que la source contient plusieurs valeurs dans la colonne `model`."
        )

    st.dataframe(
        model_results[
            [
                "model",
                "questions",
                "bonnes_reponses",
                "precision_pct",
                "latence_moyenne",
                "latence_mediane",
            ]
        ].rename(
            columns={
                "model": "Modèle",
                "questions": "Questions",
                "bonnes_reponses": "Bonnes réponses",
                "precision_pct": "Précision (%)",
                "latence_moyenne": "Latence moyenne (s)",
                "latence_mediane": "Latence médiane (s)",
            }
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Précision (%)": st.column_config.NumberColumn(format="%.2f"),
            "Latence moyenne (s)": st.column_config.NumberColumn(format="%.3f"),
            "Latence médiane (s)": st.column_config.NumberColumn(format="%.3f"),
        },
    )

    if len(model_results) > 1:
        model_chart = px.scatter(
            model_results,
            x="latence_moyenne",
            y="precision_pct",
            size="questions",
            hover_name="model",
            labels={
                "latence_moyenne": "Latence moyenne (s)",
                "precision_pct": "Précision (%)",
            },
            range_y=[0, 100],
        )
        st.plotly_chart(model_chart, width="stretch")

with details_tab:
    st.subheader("Qualité d'exécution")
    execution_columns = st.columns(3)
    execution_columns[0].metric("Résultats exploitables", f"{len(successful):,}".replace(",", " "))
    execution_columns[1].metric("Erreurs techniques", f"{len(failed):,}".replace(",", " "))
    execution_columns[2].metric(
        "Réponses incorrectes", f"{int((~successful['ai_correct'].fillna(False)).sum()):,}".replace(",", " ")
    )

    if not failed.empty:
        st.subheader("Erreurs techniques")
        error_summary = (
            failed.assign(error=failed["error"].fillna("Erreur non renseignée"))
            .groupby(["model", "error"], dropna=False)
            .size()
            .reset_index(name="occurrences")
            .sort_values("occurrences", ascending=False)
        )
        st.dataframe(error_summary, hide_index=True, width="stretch")

    st.subheader("Explorer les questions")
    only_incorrect = st.toggle("Afficher uniquement les réponses incorrectes")
    detail_data = successful
    if only_incorrect:
        detail_data = detail_data[~detail_data["ai_correct"].fillna(False)]

    displayed_columns = [
        "model",
        "category",
        "difficulty",
        "question_clean",
        "ai_answer",
        "correct_answer_clean",
        "ai_correct",
        "response_time",
    ]
    st.dataframe(
        detail_data[displayed_columns].sort_values("response_time", ascending=False),
        hide_index=True,
        width="stretch",
        column_config={
            "response_time": st.column_config.NumberColumn("Temps (s)", format="%.3f"),
            "ai_correct": st.column_config.CheckboxColumn("Correcte"),
        },
    )
    st.download_button(
        "Télécharger la sélection en CSV",
        detail_data[displayed_columns].to_csv(index=False).encode("utf-8"),
        file_name="benchmark_filtre.csv",
        mime="text/csv",
    )

import logging
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import plotly.express as px
from pathlib import Path

logger = logging.getLogger(__name__)


class TicketVisualizer:
    def __init__(self, analyzer, output_dir="output"):
        self.analyzer = analyzer
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

    def plot_spending_by_store(self):
        summary = self.analyzer.summary_by_store()

        if summary.empty:
            logger.warning("Pas de donnees pour le graphique par enseigne")
            return

        total_by_store = summary[('total', 'sum')].sort_values(ascending=False)

        fig = px.bar(
            x=total_by_store.index,
            y=total_by_store.values,
            title='Depenses totales par enseigne',
            labels={'x': 'Enseigne', 'y': 'Montant (EUR)'},
            text_auto='.2f'
        )

        output_file = self.output_dir / 'spending_by_store.html'
        fig.write_html(str(output_file))
        logger.info(f"Graphique cree: {output_file}")

    def plot_spending_trend(self):
        trend = self.analyzer.trend_by_date()

        if trend.empty:
            logger.warning("Pas de donnees pour la tendance temporelle")
            return

        daily_spending = trend[('total', 'sum')]

        fig = px.line(
            x=daily_spending.index,
            y=daily_spending.values,
            title='Tendance des depenses (montant par jour)',
            labels={'x': 'Date', 'y': 'Montant (EUR)'},
            markers=True
        )

        output_file = self.output_dir / 'spending_trend.html'
        fig.write_html(str(output_file))
        logger.info(f"Graphique cree: {output_file}")

    def plot_category_distribution(self):
        summary = self.analyzer.summary_by_category()

        if summary.empty:
            logger.warning("Pas de donnees pour le graphique par categorie")
            return

        category_spending = summary[('item_price', 'sum')].sort_values(ascending=False)

        fig = px.pie(
            values=category_spending.values,
            names=category_spending.index,
            title='Distribution des depenses par categorie alimentaire'
        )

        output_file = self.output_dir / 'category_distribution.html'
        fig.write_html(str(output_file))
        logger.info(f"Graphique cree: {output_file}")

    def plot_store_comparison(self):
        trend = self.analyzer.trend_by_store_and_date()

        if trend.empty:
            logger.warning("Pas de donnees pour la comparaison enseigne/date")
            return

        fig = go.Figure()

        for store in trend.columns:
            fig.add_trace(go.Scatter(
                x=trend.index,
                y=trend[store],
                mode='lines+markers',
                name=store
            ))

        fig.update_layout(
            title='Evolution des depenses par enseigne',
            xaxis_title='Date',
            yaxis_title='Montant (EUR)',
            hovermode='x unified'
        )

        output_file = self.output_dir / 'store_comparison.html'
        fig.write_html(str(output_file))
        logger.info(f"Graphique cree: {output_file}")

    def plot_price_heatmap(self):
        price_matrix = self.analyzer.price_by_category_and_store()

        if price_matrix.empty:
            logger.warning("Pas de donnees pour la heatmap")
            return

        fig = go.Figure(data=go.Heatmap(
            z=price_matrix.values,
            x=price_matrix.columns,
            y=price_matrix.index,
            colorscale='Viridis',
            text=price_matrix.values,
            texttemplate='%.2f'
        ))

        fig.update_layout(
            title='Prix moyen par categorie et enseigne',
            xaxis_title='Enseigne',
            yaxis_title='Categorie'
        )

        output_file = self.output_dir / 'price_heatmap.html'
        fig.write_html(str(output_file))
        logger.info(f"Graphique cree: {output_file}")

    def generate_all_visualizations(self):
        logger.info("Generation des visualisations...")

        self.plot_spending_by_store()
        self.plot_spending_trend()
        self.plot_category_distribution()
        self.plot_store_comparison()
        self.plot_price_heatmap()

        logger.info("Visualisations terminees!")
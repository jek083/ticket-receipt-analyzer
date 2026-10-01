import logging
import pandas as pd

logger = logging.getLogger(__name__)

EXCEL_CATEGORY_LABELS = {
    'fruits_legumes': 'Fruits et légumes',
    'viandes': 'Viandes',
    'glaces': 'Glaces',
    'produits_laitiers': 'Produits laitiers',
    'boulangerie': 'Boulangerie',
    'boissons': 'Boissons',
    'surgelés': 'Surgelés',
    'epicerie': 'Épicerie',
    'autre': 'Autre',
}


def _rename_multiindex_columns(dataframe, labels):
    result = dataframe.copy()
    if result.columns.empty:
        return result
    result.columns = pd.MultiIndex.from_tuples(
        [labels.get(tuple(column), tuple(column)) for column in result.columns]
    )
    return result


class TicketAnalyzer:
    def __init__(self, tickets_list):
        self.tickets = tickets_list
        self.df = self._build_dataframe()

    def _build_dataframe(self):
        data = []
        columns = [
            'date',
            'store',
            'total',
            'filename',
            'nb_items',
            'item_name',
            'item_price',
            'category',
        ]

        for ticket in self.tickets:
            items = ticket.get('articles')
            if items is None:
                items = ticket.get('items', [])
            store = ticket.get('enseigne', ticket.get('store'))
            total = ticket.get('total_ticket', ticket.get('total'))
            filename = ticket.get('fichier_source', ticket.get('filename'))

            data.append({
                'date': ticket.get('date'),
                'store': store,
                'total': total,
                'filename': filename,
                'nb_items': len(items),
                'item_name': None,
                'item_price': None,
                'category': None,
            })

            for item in items:
                data.append({
                    'date': ticket.get('date'),
                    'store': store,
                    'total': None,
                    'filename': filename,
                    'nb_items': None,
                    'item_name': item.get('article', item.get('name')),
                    'item_price': item.get('prix_total', item.get('price')),
                    'category': item.get('categorie', item.get('category')),
                })

        return pd.DataFrame(data, columns=columns)

    def summary_by_store(self):
        if self.df.empty:
            return pd.DataFrame()

        df_totals = self.df[self.df['total'].notna()]
        return df_totals.groupby('store').agg({
            'total': ['count', 'sum', 'mean', 'min', 'max'],
            'nb_items': 'mean'
        }).round(2)

    def summary_by_category(self):
        if self.df.empty:
            return pd.DataFrame()

        df_items = self.df[self.df['item_price'].notna()]
        return df_items.groupby('category').agg({
            'item_price': ['count', 'sum', 'mean'],
            'item_name': lambda x: ', '.join(x.unique()[:3])
        }).round(2)

    def trend_by_date(self):
        if self.df.empty:
            return pd.DataFrame()

        df_totals = self.df[self.df['total'].notna()].copy()
        if df_totals.empty:
            return pd.DataFrame()

        return df_totals.groupby('date').agg({
            'total': ['sum', 'count', 'mean']
        }).round(2)

    def trend_by_store_and_date(self):
        if self.df.empty:
            return pd.DataFrame()

        df_totals = self.df[self.df['total'].notna()].copy()
        if df_totals.empty:
            return pd.DataFrame()

        return df_totals.pivot_table(
            values='total',
            index='date',
            columns='store',
            aggfunc='sum'
        ).round(2)

    def top_items(self, n=10):
        if self.df.empty:
            return pd.DataFrame()

        df_items = self.df[self.df['item_name'].notna()]
        return df_items['item_name'].value_counts().head(n)

    def price_by_category_and_store(self):
        if self.df.empty:
            return pd.DataFrame()

        df_items = self.df[self.df['item_price'].notna()]
        if df_items.empty:
            return pd.DataFrame()

        return df_items.pivot_table(
            values='item_price',
            index='category',
            columns='store',
            aggfunc='mean'
        ).round(2)

    def export_to_excel(self, filepath):
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            summary_by_store = self.summary_by_store().rename_axis('Enseigne')
            _rename_multiindex_columns(
                summary_by_store,
                {
                    ('total', 'count'): ('Tickets', 'Nombre'),
                    ('total', 'sum'): ('Dépenses par ticket (€)', 'Total'),
                    ('total', 'mean'): ('Dépenses par ticket (€)', 'Moyenne'),
                    ('total', 'min'): ('Dépenses par ticket (€)', 'Minimum'),
                    ('total', 'max'): ('Dépenses par ticket (€)', 'Maximum'),
                    ('nb_items', 'mean'): ('Articles par ticket', 'Moyenne'),
                },
            ).to_excel(writer, sheet_name='Par enseigne')

            summary_by_category = self.summary_by_category().rename_axis('Catégorie')
            summary_by_category.index = summary_by_category.index.map(
                lambda category: EXCEL_CATEGORY_LABELS.get(category, category)
                if isinstance(category, str)
                else category
            )
            _rename_multiindex_columns(
                summary_by_category,
                {
                    ('item_price', 'count'): ('Articles', 'Nombre'),
                    ('item_price', 'sum'): ('Dépenses (€)', 'Total'),
                    ('item_price', 'mean'): ('Prix par article (€)', 'Moyen'),
                    ('item_name', '<lambda>'): ('Exemples d’articles', 'Noms'),
                },
            ).to_excel(writer, sheet_name='Par catégorie')

            trend_by_date = self.trend_by_date().rename_axis('Date')
            _rename_multiindex_columns(
                trend_by_date,
                {
                    ('total', 'sum'): ('Dépenses (€)', 'Total'),
                    ('total', 'count'): ('Tickets', 'Nombre'),
                    ('total', 'mean'): ('Dépense par ticket (€)', 'Moyenne'),
                },
            ).to_excel(writer, sheet_name='Tendance temporelle')

            trend_by_store_and_date = self.trend_by_store_and_date().rename_axis(
                'Date'
            )
            trend_by_store_and_date.columns.name = 'Enseigne'
            trend_by_store_and_date.to_excel(
                writer,
                sheet_name='Évolution par enseigne',
            )

            price_by_category_and_store = self.price_by_category_and_store()
            price_by_category_and_store.index.name = 'Catégorie'
            price_by_category_and_store.index = price_by_category_and_store.index.map(
                lambda category: EXCEL_CATEGORY_LABELS.get(category, category)
                if isinstance(category, str)
                else category
            )
            price_by_category_and_store.columns.name = 'Enseigne'
            price_by_category_and_store.to_excel(
                writer,
                sheet_name='Prix par catégorie et enseigne',
            )

            top_items = self.top_items(20)
            top_items.index.name = 'Article'
            top_items.name = 'Nombre d’achats'
            top_items.to_excel(writer, sheet_name='Top 20 des articles')

        logger.info(f"Fichier Excel créé: {filepath}")

    def print_summary(self):
        print("\n" + "=" * 60)
        print("ANALYSE DES TICKETS DE CAISSE")
        print("=" * 60)

        print(f"\nNombre de tickets: {len(self.tickets)}")

        df_totals = self.df[self.df['total'].notna()]
        if not df_totals.empty:
            print(f"Depense totale: {df_totals['total'].sum():.2f}")
            print(f"Depense moyenne par ticket: {df_totals['total'].mean():.2f}")
            print(f"Depense min/max: {df_totals['total'].min():.2f} / {df_totals['total'].max():.2f}")

        print("\n--- Par enseigne ---")
        print(self.summary_by_store())

        print("\n--- Par categorie ---")
        print(self.summary_by_category())

        print("\n" + "=" * 60 + "\n")

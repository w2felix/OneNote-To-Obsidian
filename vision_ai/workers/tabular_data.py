"""Tabular data worker: schema extraction + AI interpretation for XLSX, CSV, TSV files."""

import logging
from io import BytesIO
from pathlib import Path

from vision_ai.workers.base import VisionWorker, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry

logger = logging.getLogger(__name__)

MAX_SAMPLE_ROWS = 3
MAX_COLUMNS_DISPLAY = 30

INTERPRETATION_PROMPT = """You are a data analyst reviewing a spreadsheet embedded in a knowledge base note.

Page context: {page_title} / {section_path}

Here is the schema and sample data:
{schema_text}

Write a brief (3-5 sentence) narrative interpretation:
1. What does this data represent? (study type, measurements, patient cohort, etc.)
2. What are the key variables/endpoints?
3. Any notable patterns visible from the schema (high missingness, categorical variables, ranges)?

Be concise and specific. Write in markdown."""


class TabularDataWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> str:
        filename = group.filenames[0]
        data = images[filename]
        ext = Path(filename).suffix.lower()

        try:
            import pandas as pd
        except ImportError:
            logger.warning("pandas not available, skipping tabular_data analysis")
            return ""

        try:
            df = self._read_data(data, ext, filename)
        except Exception as e:
            logger.warning(f"Failed to read {filename}: {e}")
            return ""

        schema_text = self._format_schema(df, filename, page_context)
        interpretation = self._generate_interpretation(schema_text, page_context)
        if interpretation:
            return schema_text + '\n\n## Interpretation\n\n' + interpretation
        return schema_text

    def _generate_interpretation(self, schema_text: str, ctx: PageContext) -> str:
        """Call Claude to generate a brief narrative interpretation of the data."""
        prompt = INTERPRETATION_PROMPT.format(
            page_title=ctx.page_title,
            section_path=ctx.section_path,
            schema_text=schema_text[:2000],
        )
        try:
            return api_call_with_retry(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
            )
        except Exception as e:
            logger.warning(f"Interpretation generation failed: {e}")
            return ""

    def _read_data(self, data: bytes, ext: str, filename: str):
        import pandas as pd
        buf = BytesIO(data)

        if ext == '.csv':
            return pd.read_csv(buf, nrows=100)
        elif ext == '.tsv':
            return pd.read_csv(buf, sep='\t', nrows=100)
        elif ext == '.xlsx':
            xls = pd.ExcelFile(buf)
            sheet = xls.sheet_names[0]
            df = pd.read_excel(buf, sheet_name=sheet, nrows=100)
            df.attrs['_sheets'] = xls.sheet_names
            return df
        else:
            raise ValueError(f"Unsupported extension: {ext}")

    def _format_schema(self, df, filename: str, ctx: PageContext) -> str:
        lines = []
        stem = Path(filename).stem
        n_rows = len(df)
        n_cols = len(df.columns)
        sheets = df.attrs.get('_sheets', None)

        row_estimate = f"{n_rows}+" if n_rows >= 100 else str(n_rows)

        lines.append(f"# Data File: {stem}")
        lines.append("")
        lines.append(f"**Rows**: {row_estimate} | **Columns**: {n_cols}")
        if sheets and len(sheets) > 1:
            lines.append(f"**Sheets**: {', '.join(sheets[:10])}")
        lines.append("")

        # Context-based purpose
        context_text = f"{ctx.page_title} {ctx.section_path}".strip()
        if context_text:
            lines.append(f"**Context**: {context_text}")
            lines.append("")

        # Column schema table
        lines.append("## Schema")
        lines.append("")
        lines.append("| Column | Type | Non-null | Sample |")
        lines.append("| --- | --- | --- | --- |")

        for col in list(df.columns)[:MAX_COLUMNS_DISPLAY]:
            dtype = str(df[col].dtype)
            non_null = df[col].notna().sum()
            sample = ""
            first_valid = df[col].dropna().head(1)
            if len(first_valid) > 0:
                sample = str(first_valid.iloc[0])[:50]
            lines.append(f"| {col} | {dtype} | {non_null}/{n_rows} | {sample} |")

        if n_cols > MAX_COLUMNS_DISPLAY:
            lines.append(f"| ... | ... | ... | ({n_cols - MAX_COLUMNS_DISPLAY} more columns) |")

        # Sample rows
        lines.append("")
        lines.append("## Sample Data (first 3 rows)")
        lines.append("")
        sample_df = df.head(MAX_SAMPLE_ROWS)
        try:
            lines.append(sample_df.to_markdown(index=False))
        except ImportError:
            # tabulate not installed — fall back to CSV-style
            lines.append(sample_df.to_string(index=False))

        return '\n'.join(lines)

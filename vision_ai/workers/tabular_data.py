"""Tabular data worker: schema extraction + AI interpretation for XLSX, CSV, TSV files."""

import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

from vision_ai.workers.base import VisionWorker, AnalysisResult, parse_structured_response, AttachmentGroup, PageContext
from vision_ai.client import api_call_with_retry

logger = logging.getLogger(__name__)

MAX_SAMPLE_ROWS = 3
MAX_COLUMNS_DISPLAY = 30

INTERPRETATION_PROMPT = """You are a data analyst reviewing a spreadsheet.

Page context: {page_title} / {section_path}

Here is the schema and sample data:
{schema_text}

Respond in EXACTLY this format:

TITLE: [what this dataset represents, e.g. "Patient cohort survival data"]
KEY_POINTS:
- [key variable/endpoint 1]
- [key variable/endpoint 2]
- [notable pattern from schema, e.g. high missingness, specific ranges]
BODY:
[2-3 sentence interpretation: what type of data is this, what are the key variables, any notable patterns.]"""


class TabularDataWorker(VisionWorker):

    def analyze(self, group: AttachmentGroup, images: dict[str, bytes],
                page_context: PageContext) -> Optional[AnalysisResult]:
        filename = group.filenames[0]
        data = images[filename]
        ext = Path(filename).suffix.lower()

        try:
            import pandas as pd
        except ImportError:
            logger.warning("pandas not available, skipping tabular_data analysis")
            return None

        try:
            df = self._read_data(data, ext, filename)
        except Exception as e:
            logger.warning(f"Failed to read {filename}: {e}")
            return None

        schema_text = self._format_schema(df, filename, page_context)
        result = self._generate_interpretation(schema_text, page_context)

        if result is None:
            result = AnalysisResult(
                title=Path(filename).stem,
                content_type="tabular_data",
            )

        # Add schema info to extras
        n_rows = len(df)
        n_cols = len(df.columns)
        sheets = df.attrs.get('_sheets', None)
        result.content_type = "tabular_data"
        result.extra['rows'] = f"{n_rows}+" if n_rows >= 100 else n_rows
        result.extra['columns'] = n_cols
        if sheets and len(sheets) > 1:
            result.extra['sheets'] = sheets[:10]

        # Schema goes in the body
        if not result.body:
            result.body = schema_text
        else:
            result.body = result.body + '\n\n' + schema_text

        return result

    def _generate_interpretation(self, schema_text: str, ctx: PageContext) -> Optional[AnalysisResult]:
        prompt = INTERPRETATION_PROMPT.format(
            page_title=ctx.page_title,
            section_path=ctx.section_path,
            schema_text=schema_text[:2000],
        )
        try:
            response = api_call_with_retry(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
            )
            return parse_structured_response(response, default_content_type='tabular_data')
        except Exception as e:
            logger.warning(f"Interpretation generation failed: {e}")
            return None

    def _read_data(self, data: bytes, ext: str, filename: str):
        import pandas as pd
        buf = BytesIO(data)

        if ext == '.csv':
            return pd.read_csv(buf, nrows=100)
        elif ext == '.tsv':
            return pd.read_csv(buf, sep='\t', nrows=100)
        elif ext in ('.xlsx', '.xls'):
            engine = 'xlrd' if ext == '.xls' else None
            xls = pd.ExcelFile(buf, engine=engine)
            sheet, skip = self._best_sheet(buf, xls.sheet_names, engine)
            df = pd.read_excel(buf, sheet_name=sheet, skiprows=skip, nrows=100, engine=engine)
            df.attrs['_sheets'] = xls.sheet_names
            df.attrs['_active_sheet'] = sheet
            return df
        else:
            raise ValueError(f"Unsupported extension: {ext}")

    @classmethod
    def _best_sheet(cls, buf: BytesIO, sheet_names: list, engine) -> tuple:
        """Return (sheet_name, skiprows) for the sheet most likely to hold real data.

        Scores each sheet by the maximum non-null cell count in any single row
        (the header row). The sheet with the highest score wins; ties go to the
        first sheet. Caps per-sheet scan at 200 rows for speed.
        Returns (sheet_name, skiprows_for_that_sheet).
        """
        import pandas as pd
        best_sheet, best_skip, best_score = sheet_names[0], 0, -1
        for name in sheet_names:
            buf.seek(0)
            try:
                raw = pd.read_excel(buf, sheet_name=name, header=None, nrows=200, engine=engine)
            except Exception:
                continue
            buf.seek(0)
            counts = [int(row.notna().sum()) for _, row in raw.iterrows()]
            score = max(counts) if counts else 0
            if score > best_score:
                best_score = score
                best_sheet = name
                best_skip = cls._detect_preamble_rows_from_counts(counts)
        return best_sheet, best_skip

    @staticmethod
    def _detect_preamble_rows_from_counts(counts: list) -> int:
        """Given per-row non-null counts, return the index of the header row."""
        import statistics
        if not counts:
            return 0
        max_count = max(counts)
        if max_count < 2:
            return 0
        for i, c in enumerate(counts):
            if c < 5:
                continue
            prior = counts[:i] if i > 0 else [0]
            median_prior = statistics.median(prior) or 1
            if c >= max(5, median_prior * 3):
                return i
        return counts.index(max_count)

    @classmethod
    def _detect_preamble_rows(cls, buf: BytesIO, sheet: str, engine) -> int:
        """Return the number of rows to skip before the real header row."""
        import pandas as pd
        buf.seek(0)
        raw = pd.read_excel(buf, sheet_name=sheet, header=None, nrows=200, engine=engine)
        buf.seek(0)
        counts = [int(row.notna().sum()) for _, row in raw.iterrows()]
        return cls._detect_preamble_rows_from_counts(counts)

    def _format_schema(self, df, filename: str, ctx: PageContext) -> str:
        lines = []
        n_rows = len(df)
        n_cols = len(df.columns)

        active_sheet = df.attrs.get('_active_sheet')
        all_sheets = df.attrs.get('_sheets')
        if active_sheet:
            sheet_note = f"Sheet: **{active_sheet}**"
            if all_sheets and len(all_sheets) > 1:
                sheet_note += f" (of {len(all_sheets)}: {', '.join(all_sheets[:5])})"
            lines.append(sheet_note)
            lines.append("")

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

        lines.append("")
        lines.append("## Sample Data (first 3 rows)")
        lines.append("")
        sample_df = df.head(MAX_SAMPLE_ROWS)
        try:
            lines.append(sample_df.to_markdown(index=False))
        except ImportError:
            lines.append(sample_df.to_string(index=False))

        return '\n'.join(lines)



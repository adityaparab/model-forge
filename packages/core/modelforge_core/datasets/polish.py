"""UCI Polish companies bankruptcy data (five yearly ARFF files) as a canonical dataset."""

import zipfile
from pathlib import Path

import pandas as pd
from scipy.io import arff

from modelforge_core.datasets.base import LoadedDataset, sanitize_columns, validate_canonical
from modelforge_core.datasets.download import fetch

POLISH_URL = "https://archive.ics.uci.edu/static/public/365/polish+companies+bankruptcy+data.zip"
POLISH_SHA256 = "17377929aa0b204bbf957e56462cf827c19fe4e2ce89f27dfbc77f9ea2bb16c9"
POLISH_FILES = ("1year.arff", "2year.arff", "3year.arff", "4year.arff", "5year.arff")

# Source: UCI dataset page, CC BY 4.0. Attr{i} is described by entry i-1.
POLISH_ATTRIBUTE_DESCRIPTIONS: tuple[str, ...] = (
    "net profit / total assets",
    "total liabilities / total assets",
    "working capital / total assets",
    "current assets / short-term liabilities",
    "[(cash + short-term securities + receivables - short-term liabilities)"
    " / (operating expenses - depreciation)] * 365",
    "retained earnings / total assets",
    "EBIT / total assets",
    "book value of equity / total liabilities",
    "sales / total assets",
    "equity / total assets",
    "(gross profit + extraordinary items + financial expenses) / total assets",
    "gross profit / short-term liabilities",
    "(gross profit + depreciation) / sales",
    "(gross profit + interest) / total assets",
    "(total liabilities * 365) / (gross profit + depreciation)",
    "(gross profit + depreciation) / total liabilities",
    "total assets / total liabilities",
    "gross profit / total assets",
    "gross profit / sales",
    "(inventory * 365) / sales",
    "sales (n) / sales (n-1)",
    "profit on operating activities / total assets",
    "net profit / sales",
    "gross profit (in 3 years) / total assets",
    "(equity - share capital) / total assets",
    "(net profit + depreciation) / total liabilities",
    "profit on operating activities / financial expenses",
    "working capital / fixed assets",
    "logarithm of total assets",
    "(total liabilities - cash) / sales",
    "(gross profit + interest) / sales",
    "(current liabilities * 365) / cost of products sold",
    "operating expenses / short-term liabilities",
    "operating expenses / total liabilities",
    "profit on sales / total assets",
    "total sales / total assets",
    "(current assets - inventories) / long-term liabilities",
    "constant capital / total assets",
    "profit on sales / sales",
    "(current assets - inventory - receivables) / short-term liabilities",
    "total liabilities / ((profit on operating activities + depreciation) * (12/365))",
    "profit on operating activities / sales",
    "rotation receivables + inventory turnover in days",
    "(receivables * 365) / sales",
    "net profit / inventory",
    "(current assets - inventory) / short-term liabilities",
    "(inventory * 365) / cost of products sold",
    "EBITDA (profit on operating activities - depreciation) / total assets",
    "EBITDA (profit on operating activities - depreciation) / sales",
    "current assets / total liabilities",
    "short-term liabilities / total assets",
    "(short-term liabilities * 365) / cost of products sold",
    "equity / fixed assets",
    "constant capital / fixed assets",
    "working capital",
    "(sales - cost of products sold) / sales",
    "(current assets - inventory - short-term liabilities) / (sales - gross profit - depreciation)",
    "total costs / total sales",
    "long-term liabilities / equity",
    "sales / inventory",
    "sales / receivables",
    "(short-term liabilities * 365) / sales",
    "sales / short-term liabilities",
    "sales / fixed assets",
)

_FEATURE_COLUMNS = [f"Attr{i}" for i in range(1, 65)]


def parse_arff(path: Path) -> pd.DataFrame:
    """Read an ARFF file: numeric attributes as float64 ('?' -> NaN), nominal `class` as int64."""
    data, _meta = arff.loadarff(path)
    frame = pd.DataFrame(data)
    if "class" in frame.columns:
        frame["class"] = frame["class"].str.decode("ascii").astype("int64")
    return frame


class PolishBankruptcyAdapter:
    name = "polish_bankruptcy"

    def load(self, cache_dir: Path) -> LoadedDataset:
        root = cache_dir / "polish"
        paths = [root / fname for fname in POLISH_FILES]
        if not all(p.exists() for p in paths):
            archive = fetch(POLISH_URL, POLISH_SHA256, root / "polish.zip")
            with zipfile.ZipFile(archive) as zf:
                for fname in POLISH_FILES:
                    (root / fname).write_bytes(zf.read(fname))

        parts: list[pd.DataFrame] = []
        for vintage, path in enumerate(paths, start=1):
            part = parse_arff(path)
            part["vintage"] = vintage
            part["obligor_id"] = [f"p{vintage}_{i:05d}" for i in range(len(part))]
            part["default_flag"] = part["class"]
            parts.append(part)
        raw = pd.concat(parts, ignore_index=True)

        descriptions = dict(zip(_FEATURE_COLUMNS, POLISH_ATTRIBUTE_DESCRIPTIONS, strict=True))
        frame, dictionary = sanitize_columns(
            raw, _FEATURE_COLUMNS, dataset=self.name, descriptions=descriptions
        )
        validate_canonical(frame)
        return LoadedDataset(name=self.name, frame=frame, dictionary=dictionary, oot_capable=True)

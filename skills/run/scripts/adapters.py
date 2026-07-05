"""adapters.py — source-format adapters for the Data Architect stage.

Each adapter turns a raw file into a list of row-dicts (one logical table). The Architect
then profiles + types the columns (profile.py) and lands them in bronze.

Design: STDLIB-FIRST. CSV / JSON / NDJSON / XML / XLSX parse with the standard library
only (XLSX is a zip of XML — we read it without openpyxl). PDF and images are OPTIONAL:
they use pypdf / pytesseract if present and degrade to a clear note if not, so the skill
never hard-fails on a missing heavy dependency.

Every adapter returns: (rows: list[dict[str, str]], meta: dict). Values are strings at this
stage (raw); profile.py infers real types. meta carries {"kind": "tabular"|"document", ...}.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

TABULAR_EXT = {".csv", ".tsv", ".json", ".ndjson", ".jsonl", ".xml", ".xlsx"}
DOCUMENT_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
SUPPORTED_EXT = TABULAR_EXT | DOCUMENT_EXT


class AdapterError(Exception):
    pass


# --- tabular -----------------------------------------------------------------

def _csv(path: pathlib.Path, delimiter: str) -> tuple[list[dict], dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=delimiter + ",;\t|")
            delim = dialect.delimiter
        except csv.Error:
            delim = delimiter
        rows = list(csv.DictReader(f, delimiter=delim))
    return rows, {"kind": "tabular", "delimiter": delim}


def _json(path: pathlib.Path) -> tuple[list[dict], dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        # find the first list-of-objects value, else wrap the single object as one row
        arr = next((v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
        rows = arr if arr is not None else [data]
    else:
        raise AdapterError("JSON root must be an array or object")
    rows = [_flatten(r) for r in rows]
    return rows, {"kind": "tabular"}


def _ndjson(path: pathlib.Path) -> tuple[list[dict], dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(_flatten(json.loads(line)))
    return rows, {"kind": "tabular"}


def _xml(path: pathlib.Path) -> tuple[list[dict], dict]:
    root = ET.parse(path).getroot()
    # Heuristic: the repeated child tag is the record; each record's children/attribs are columns.
    children = list(root)
    if not children:
        return [_xml_record(root)], {"kind": "tabular"}
    from collections import Counter
    common = Counter(c.tag for c in children).most_common(1)[0][0]
    records = [c for c in children if c.tag == common] or children
    return [_xml_record(r) for r in records], {"kind": "tabular", "record_tag": common}


def _xml_record(elem) -> dict:
    rec: dict[str, str] = {}
    rec.update({k: v for k, v in elem.attrib.items()})
    for child in elem:
        if list(child):  # nested — flatten one level with a dotted key
            for k, v in child.attrib.items():
                rec[f"{child.tag}.{k}"] = v
            if (child.text or "").strip():
                rec[child.tag] = child.text.strip()
        else:
            rec[child.tag] = (child.text or "").strip()
    if not rec and (elem.text or "").strip():
        rec[elem.tag] = elem.text.strip()
    return rec


def _xlsx(path: pathlib.Path) -> tuple[list[dict], dict]:
    """Read the first worksheet of an .xlsx WITHOUT openpyxl (it is a zip of XML parts)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sst.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
        sheet_name = next((n for n in z.namelist() if n.startswith("xl/worksheets/sheet")), None)
        if not sheet_name:
            raise AdapterError("no worksheet found in xlsx")
        ws = ET.fromstring(z.read(sheet_name))
    grid: list[list[str]] = []
    for row in ws.iter("{%s}row" % ns["m"]):
        cells: list[str] = []
        for c in row.findall("m:c", ns):
            v = c.find("m:v", ns)
            text = "" if v is None else (shared[int(v.text)] if c.get("t") == "s" else (v.text or ""))
            cells.append(text)
        grid.append(cells)
    if not grid:
        return [], {"kind": "tabular", "source": "xlsx"}
    header = [h or f"col_{i}" for i, h in enumerate(grid[0])]
    rows = [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in grid[1:]]
    return rows, {"kind": "tabular", "source": "xlsx"}


def _flatten(obj: dict, prefix: str = "") -> dict:
    """Flatten one level of nested dicts with dotted keys; stringify scalars, JSON-encode lists."""
    out: dict[str, str] = {}
    if not isinstance(obj, dict):
        return {"value": _scalar(obj)}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, prefix=f"{key}."))
        elif isinstance(v, list):
            out[key] = json.dumps(v, ensure_ascii=False)
        else:
            out[key] = _scalar(v)
    return out


def _scalar(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# --- documents (optional deps) -----------------------------------------------

def _pdf(path: pathlib.Path) -> tuple[list[dict], dict]:
    try:
        import pypdf  # optional
    except ImportError:
        raise AdapterError("PDF ingest needs `pypdf` (pip install pypdf); skipped")
    reader = pypdf.PdfReader(str(path))
    rows = [{"page": str(i + 1), "text": (pg.extract_text() or "").strip()}
            for i, pg in enumerate(reader.pages)]
    return rows, {"kind": "document", "pages": len(rows)}


def _image(path: pathlib.Path) -> tuple[list[dict], dict]:
    try:
        import pytesseract  # optional
        from PIL import Image  # optional
    except ImportError:
        raise AdapterError("image OCR needs `pytesseract` + `Pillow` (and tesseract); skipped")
    text = pytesseract.image_to_string(Image.open(path)).strip()
    return [{"source": path.name, "text": text}], {"kind": "document", "ocr": True}


# --- dispatch ----------------------------------------------------------------

def ingest(path: pathlib.Path) -> tuple[list[dict], dict]:
    """Ingest one file by extension. Raises AdapterError for unsupported/missing-dep cases."""
    ext = path.suffix.lower()
    if ext == ".csv":
        return _csv(path, ",")
    if ext == ".tsv":
        return _csv(path, "\t")
    if ext == ".json":
        return _json(path)
    if ext in (".ndjson", ".jsonl"):
        return _ndjson(path)
    if ext == ".xml":
        return _xml(path)
    if ext == ".xlsx":
        return _xlsx(path)
    if ext == ".pdf":
        return _pdf(path)
    if ext in DOCUMENT_EXT:
        return _image(path)
    raise AdapterError(f"unsupported extension: {ext}")

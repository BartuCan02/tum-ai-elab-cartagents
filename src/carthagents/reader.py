"""Read source text AND coordinates; XLSX is read directly using stdlib ZIP/XML."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import re

@dataclass
class Token:
    text: str
    page: int | None = None
    bbox: tuple[float,float,float,float] | None = None
    sheet: str | None = None
    cell: str | None = None
    row: int | None = None
    col: int | None = None
    line: int | None = None
    origin: str = "text_layer"

    def pointer(self):
        if self.cell:
            return {"sheet": self.sheet, "cell": self.cell}
        return {"page": self.page, "bbox": list(self.bbox) if self.bbox else None, "origin": self.origin}

def pdf_tokens(path: Path, ocr: bool = False) -> list[Token]:
    import fitz
    result = []
    with fitz.open(path) as pdf:
        for pno, page in enumerate(pdf, 1):
            words = page.get_text("words", sort=True)
            if not words and ocr:
                try:
                    import pytesseract
                    from PIL import Image
                    pix = page.get_pixmap(matrix=fitz.Matrix(2,2))
                    img = Image.frombytes("RGB" if pix.n == 3 else "RGBA", (pix.width,pix.height), pix.samples)
                    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 6")
                    for i, word in enumerate(data["text"]):
                        if not word.strip():
                            continue
                        x,y,w,h = (data[k][i]/2 for k in ("left","top","width","height"))
                        result.append(Token(word,pno,(x,y,x+w,y+h),line=int(data["block_num"][i])*10000+int(data["par_num"][i])*100+int(data["line_num"][i]),origin="ocr"))
                except ImportError as e:
                    raise RuntimeError("OCR requested: pip install -e '.[ocr]' and install Tesseract") from e
            else:
                for x0,y0,x1,y1,text,block,line,wordno in words:
                    result.append(Token(text,pno,(x0,y0,x1,y1),line=block*1000+line))
    return result

NS = {"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships", "p":"http://schemas.openxmlformats.org/package/2006/relationships"}

def excel_tokens(path: Path) -> list[Token]:
    result=[]
    with ZipFile(path) as z:
        names=set(z.namelist())
        shared=[]
        if "xl/sharedStrings.xml" in names:
            root=ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si",NS):
                shared.append("".join(t.text or "" for t in si.findall(".//m:t",NS)))
        workbook=ET.fromstring(z.read("xl/workbook.xml"))
        relroot=ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rels={r.attrib["Id"]: r.attrib["Target"] for r in relroot}
        for sheet in workbook.findall("m:sheets/m:sheet",NS):
            target=rels[sheet.attrib[f"{{{NS['r']}}}id"]].lstrip("/")
            src=target if target.startswith("xl/") else "xl/"+target
            root=ET.fromstring(z.read(src))
            for row in root.findall("m:sheetData/m:row",NS):
                for cell in row.findall("m:c",NS):
                    addr=cell.attrib["r"]
                    v=cell.find("m:v",NS)
                    inline=cell.find("m:is",NS)
                    if cell.attrib.get("t")=="s" and v is not None:
                        value=shared[int(v.text)]
                    elif inline is not None:
                        value="".join(x.text or "" for x in inline.findall(".//m:t",NS))
                    elif v is not None:
                        value=v.text or ""
                    else:
                        continue
                    m=re.fullmatch(r"([A-Z]+)(\d+)",addr)
                    col=0
                    for char in m.group(1): col=col*26+ord(char)-64
                    result.append(Token(value,sheet=sheet.attrib["name"],cell=addr,row=int(m.group(2)),col=col,origin="excel_cell"))
    return result

def load_tokens(path: Path, ocr: bool = False) -> list[Token]:
    if path.suffix.lower()==".pdf": return pdf_tokens(path,ocr)
    if path.suffix.lower()==".xlsx": return excel_tokens(path)
    raise ValueError("Supported formats: .pdf and .xlsx")

"""Emit audit JSON and editable workbook with artifact_tool."""
import json
from pathlib import Path
from artifact_tool import Workbook, SpreadsheetFile
from .extract import Entry

COLUMNS=["Identifier","Source label","AK Anfang (cents)","Zugang (cents)","Abgang (cents)","Umbuchung (cents)","AK Ende (cents)","AfA kumuliert (cents)","AfA Jahr (cents)","Buchwert Ende (cents)","Buchwert Vorjahr (cents)"]
FIELD_COL={"ak_anfang":2,"zugang":3,"abgang":4,"umbuchung":5,"ak_ende":6,"afa_kumuliert":7,"afa_jahr":8,"buchwert_ende":9,"buchwert_vorjahr":10}

def save(source:Path, entries:list[Entry], issues:list[dict], dest:Path):
    dest.mkdir(parents=True,exist_ok=True)
    stem=source.stem
    wb=Workbook.create()
    sheets={"asset":wb.worksheets.add("Anlagenverzeichnis"),"class":wb.worksheets.add("Anlagengitter"),"subtotal":wb.worksheets.add("Totals and subtotals")}
    for s in sheets.values():
        s.get_range("A1:K1").values=[COLUMNS]
        s.get_range("A1:K1").format={"fill":"#122042","font":{"bold":True,"color":"#FFFFFF"},"row_height":28}
        s.get_range("A:A").format.column_width=20
        s.get_range("B:B").format.column_width=42
        s.get_range("C:K").format.column_width=21
        s.freeze_panes.freeze_rows(1)
    groups={k:{} for k in sheets}
    for e in entries:
        if e.kind not in groups or e.field not in FIELD_COL:continue
        key=e.identifier
        if key not in groups[e.kind]:groups[e.kind][key]=[key,e.row_label]+[None]*9
        if e.status=="ok":groups[e.kind][key][FIELD_COL[e.field]]=e.value_cents
    for kind,by_id in groups.items():
        if by_id:
            rows=list(by_id.values())
            sheets[kind].get_range_by_indexes(1,0,len(rows),11).values=rows
            sheets[kind].get_range(f"C2:K{len(rows)+1}").set_number_format("#,##0;[Red](#,##0)")
    review=wb.worksheets.add("Review flags")
    review.get_range("A1:D1").values=[["Identifier","Type","Issue","Detail"]]
    review.get_range("A1:D1").format={"fill":"#122042","font":{"bold":True,"color":"#FFFFFF"}}
    review.get_range("A:D").format.column_width=33
    flagged=[[e.identifier,e.kind,e.field,e.reason] for e in entries if e.status!="ok"]
    flagged += [[e.get("identifier"),e.get("kind"),e.get("check"),"Mismatch: source values left untouched"] for e in issues]
    if flagged:review.get_range_by_indexes(1,0,len(flagged),4).values=flagged
    SpreadsheetFile.export_xlsx(wb).save(str(dest/(stem+"_Arbeitsversion.xlsx")))
    report={"source_file":source.name,"unit":"cents","schema_note":"Starter schema; map to official Arbeitsversion once provided","records":[e.dump() for e in entries],"checks":issues,"summary":{"mapped":sum(e.status=="ok" for e in entries),"flagged":sum(e.status!="ok" for e in entries),"inconsistencies":len(issues)}}
    (dest/(stem+"_audit.json")).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")

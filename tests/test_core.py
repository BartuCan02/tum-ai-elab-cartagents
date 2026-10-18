from carthagents.numbers import parse_cents
from carthagents.reader import Token
from carthagents.extract import infer_entries, checks, Entry

def test_german_amount():
    assert parse_cents("19.425,96")==1942596
    assert parse_cents("1.234,00-")==-123400
    assert parse_cents("(1,234.56)")==-123456
    assert parse_cents("?") is None

def test_excel_evidence():
    t=[Token("Anlage",sheet="Sheet1",cell="A1",row=1,col=1),Token("Zugang",sheet="Sheet1",cell="B1",row=1,col=2),Token("Abgang",sheet="Sheet1",cell="C1",row=1,col=3),Token("210004",sheet="Sheet1",cell="A2",row=2,col=1),Token("19.425,96",sheet="Sheet1",cell="B2",row=2,col=2),Token("0,00",sheet="Sheet1",cell="C2",row=2,col=3)]
    found=infer_entries(t)
    assert len(found)==2
    assert found[0].value_cents==1942596
    assert found[0].pointer["cell"]=="B2"

def test_inconsistent_not_fixed():
    vals=[("ak_anfang",10000),("zugang",2000),("abgang",1000),("umbuchung",0),("ak_ende",9999)]
    entries=[Entry("asset","1","asset",""+f,v,str(v),{},"ok") for f,v in vals]
    assert checks(entries)[0]["status"]=="mismatch"

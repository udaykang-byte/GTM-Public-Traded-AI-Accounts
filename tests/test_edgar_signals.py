from datetime import date, timedelta

from pipeline import edgar_signals as es

COMPANY = {"cik": 999, "ticker": "TST"}
RECENT = date.today() - timedelta(days=30)


class FakeFiling:
    def __init__(self, items, text="", filing_date=RECENT):
        self.form = "8-K"
        self.filing_date = filing_date
        self.items = items
        self._text = text
        self.text_calls = 0
        self.accession_no = "0000000000-26-000001"

    def text(self):
        self.text_calls += 1
        return self._text


class FakeCompany:
    def __init__(self, filings):
        self._filings = filings

    def get_filings(self, form=None):
        return self._filings


def test_filing_items_handles_list_and_string():
    assert es._filing_items(FakeFiling(["5.02", "9.01"])) == {"5.02", "9.01"}
    assert es._filing_items(FakeFiling("Items 2.05, 9.01")) == {"2.05", "9.01"}
    assert es._filing_items(FakeFiling(None)) == set()


def test_irrelevant_items_skip_download():
    f = FakeFiling(["7.01", "9.01"], text="press release about a conference")
    signals = es.eightk_signals(FakeCompany([f]), COMPANY)
    assert signals == []
    assert f.text_calls == 0


def test_item_502_yields_e3():
    text = ("On June 1, 2026 the board appointed Jane Roe as the company's "
            "chief financial officer, effective immediately.")
    f = FakeFiling(["5.02", "9.01"], text=text)
    signals = es.eightk_signals(FakeCompany([f]), COMPANY)
    assert [s.type for s in signals] == ["E3"]
    assert "CFO" in signals[0].title
    assert f.text_calls == 1


def test_item_205_yields_e4():
    text = "The company committed to a restructuring plan to reduce operating costs."
    f = FakeFiling(["2.05"], text=text)
    signals = es.eightk_signals(FakeCompany([f]), COMPANY)
    assert [s.type for s in signals] == ["E4"]


def test_old_filing_stops_scan():
    old = FakeFiling(["5.02"], text="appointed chief executive officer",
                     filing_date=date.today() - timedelta(days=400))
    signals = es.eightk_signals(FakeCompany([old]), COMPANY)
    assert signals == []
    assert old.text_calls == 0

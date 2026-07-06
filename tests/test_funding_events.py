from datetime import date, timedelta

from pipeline import funding_events as fe

COMPANY = {"cik": 999, "ticker": "TST"}
RECENT = date.today() - timedelta(days=30)


class FakeFiling:
    def __init__(self, form, text="", filing_date=RECENT, items=None, accession="0001234-26-000042"):
        self.form = form
        self.filing_date = filing_date
        self.items = items
        self._text = text
        self.text_calls = 0
        self.accession_no = accession

    def text(self):
        self.text_calls += 1
        return self._text


class FakeCompany:
    def __init__(self, filings_by_form):
        self._by_form = filings_by_form

    def get_filings(self, form=None):
        return self._by_form.get(form, [])


def test_extract_amount_million_words():
    assert fe._extract_amount("gross proceeds of approximately $12.5 million") == 12_500_000


def test_extract_amount_full_digits():
    assert fe._extract_amount("aggregate offering price of $50,000,000") == 50_000_000


def test_extract_amount_ignores_par_value_noise():
    assert fe._extract_amount("par value $0.001 per share; no proceeds language") is None


def test_424b5_yields_follow_on_angle():
    f = FakeFiling("424B5", text="We estimate gross proceeds of $12.0 million. "
                                 "We intend to use the net proceeds for sales expansion.")
    out = fe.funding_angles(FakeCompany({"424B5": [f]}), COMPANY)
    assert len(out) == 1
    a = out[0]
    assert a.family.value == "funding"
    assert a.details["instrument"] == "follow_on"
    assert a.details["amount_usd"] == 12_000_000
    assert a.fingerprint == "funding:0001234-26-000042"
    assert a.event_date == RECENT
    assert "use the net proceeds" in (a.evidence_quote or "")


def test_atm_detected_from_prospectus_text():
    f = FakeFiling("424B5", text="This prospectus relates to our at-the-market offering program "
                                 "with aggregate offering price of up to $25 million.")
    out = fe.funding_angles(FakeCompany({"424B5": [f]}), COMPANY)
    assert out[0].details["instrument"] == "atm"


def test_s3_shelf_recorded_even_without_amount():
    f = FakeFiling("S-3", text="")
    out = fe.funding_angles(FakeCompany({"S-3": [f]}), COMPANY)
    assert out[0].details["instrument"] == "shelf"
    assert out[0].details["amount_usd"] is None


def test_8k_302_yields_pipe():
    f = FakeFiling("8-K", items=["3.02", "9.01"],
                   text="entered into a securities purchase agreement for gross proceeds of $8 million")
    out = fe.funding_angles(FakeCompany({"8-K": [f]}), COMPANY)
    assert out[0].details["instrument"] == "pipe"


def test_8k_101_credit_agreement_yields_debt():
    f = FakeFiling("8-K", items=["1.01"],
                   text="entered into a credit agreement providing a term loan with principal amount of $20 million")
    out = fe.funding_angles(FakeCompany({"8-K": [f]}), COMPANY)
    assert out[0].details["instrument"] == "debt"


def test_8k_101_without_financing_language_skipped():
    f = FakeFiling("8-K", items=["1.01"], text="entered into a lease agreement for office space")
    assert fe.funding_angles(FakeCompany({"8-K": [f]}), COMPANY) == []


def test_8k_other_items_skip_download():
    f = FakeFiling("8-K", items=["7.01"], text="conference presentation")
    assert fe.funding_angles(FakeCompany({"8-K": [f]}), COMPANY) == []
    assert f.text_calls == 0


def test_old_filing_beyond_window_skipped():
    f = FakeFiling("424B5", text="gross proceeds of $12 million",
                   filing_date=date.today() - timedelta(days=400))
    assert fe.funding_angles(FakeCompany({"424B5": [f]}), COMPANY) == []

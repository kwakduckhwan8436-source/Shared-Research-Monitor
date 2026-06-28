"""KIS 잔고조회 파싱 + 계좌번호 파싱 회귀."""
from app.providers.kis import KISProvider


def test_parse_holdings_filters_zero_and_computes():
    body = {
        "output1": [
            {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "50",
             "pchs_avg_pric": "68000", "prpr": "74000", "evlu_amt": "3700000",
             "pchs_amt": "3400000", "evlu_pfls_amt": "300000", "evlu_pfls_rt": "8.82"},
            {"pdno": "000660", "prdt_name": "SK하이닉스", "hldg_qty": "10",
             "pchs_avg_pric": "180000", "prpr": "170000", "evlu_amt": "1700000",
             "pchs_amt": "1800000", "evlu_pfls_amt": "-100000", "evlu_pfls_rt": "-5.56"},
            {"pdno": "035720", "prdt_name": "매도완료", "hldg_qty": "0",
             "prpr": "0", "evlu_amt": "0", "pchs_amt": "0", "evlu_pfls_amt": "0",
             "evlu_pfls_rt": "0", "pchs_avg_pric": "0"},
        ],
        "output2": [{"tot_evlu_amt": "5400000", "pchs_amt_smtl_amt": "5200000",
                     "evlu_pfls_smtl_amt": "200000", "dnca_tot_amt": "1000000"}],
    }
    r = KISProvider.parse_holdings(body)
    assert len(r["positions"]) == 2                 # 수량 0 제외
    p = r["positions"][0]
    assert p["symbol"] == "005930" and p["qty"] == 50
    assert p["pnl"] == 300000 and p["pnl_pct"] == 8.82
    assert r["positions"][1]["pnl"] == -100000      # 손실 음수 보존
    assert r["summary"]["eval_total"] == 5400000
    assert r["summary"]["cash"] == 1000000


def test_parse_holdings_empty():
    r = KISProvider.parse_holdings({"output1": [], "output2": []})
    assert r["positions"] == []
    assert r["summary"]["eval_total"] == 0


def test_symbol_zero_padded():
    body = {"output1": [{"pdno": "5930", "prdt_name": "X", "hldg_qty": "1",
                         "prpr": "100", "pchs_avg_pric": "90", "evlu_amt": "100",
                         "pchs_amt": "90", "evlu_pfls_amt": "10", "evlu_pfls_rt": "11"}],
            "output2": [{}]}
    r = KISProvider.parse_holdings(body)
    assert r["positions"][0]["symbol"] == "005930"   # 6자리 패딩

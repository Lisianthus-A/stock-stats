import json
import unittest
from datetime import date

import core
from core import (
    BUY,
    SELL,
    ClosedPair,
    ValidationError,
    build_summary,
    human_days,
    normalize_trade,
    pair_trades,
    parse_date,
    parse_price,
)


def make_trade(tid, code, side, trade_date, price, name=None, seq=0, note=""):
    return core.Trade(
        id=tid,
        code=code,
        name=name or f"{code}股",
        side=side,
        trade_date=parse_date(trade_date),
        price=price,
        note=note,
        seq=seq,
    )


class ParseDateTests(unittest.TestCase):
    def test_accepts_common_separators(self):
        expected = date(2024, 3, 7)
        for value in ("2024-03-07", "2024/3/7", "2024.03.07", " 2024-3-7 "):
            self.assertEqual(parse_date(value), expected, value)

    def test_rejects_bad_input(self):
        for value in ("", None, "abc", "2024-13-01", "2024-02-30", "24-3-7"):
            with self.assertRaises(ValidationError, msg=repr(value)):
                parse_date(value)


class ParsePriceTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_price("10.5"), 10.5)
        self.assertEqual(parse_price(0.01), 0.01)
        self.assertEqual(parse_price(" 3 "), 3.0)

    def test_rejects_non_positive_and_garbage(self):
        for value in ("0", "-1", "abc", None, "", "nan", "inf"):
            with self.assertRaises(ValidationError, msg=repr(value)):
                parse_price(value)


class NormalizeTradeTests(unittest.TestCase):
    def test_normalizes_chinese_side_and_trims(self):
        result = normalize_trade(
            {"code": " 600519 ", "name": " 贵州茅台 ", "side": "买入",
             "trade_date": "2024/1/2", "price": "1680.5", "note": " 建仓 "}
        )
        self.assertEqual(result, {
            "code": "600519", "name": "贵州茅台", "side": BUY,
            "trade_date": "2024-01-02", "price": 1680.5, "note": "建仓",
        })

    def test_requires_code_and_name(self):
        base = {"code": "600519", "name": "贵州茅台", "side": BUY,
                "trade_date": "2024-01-02", "price": 10}
        for field in ("code", "name"):
            payload = dict(base)
            payload[field] = "  "
            with self.assertRaises(ValidationError):
                normalize_trade(payload)

    def test_rejects_unknown_side(self):
        with self.assertRaises(ValidationError):
            normalize_trade({"code": "1", "name": "x", "side": "sell short",
                             "trade_date": "2024-01-02", "price": 10})


class HumanDaysTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(human_days(0), "0 天")
        self.assertEqual(human_days(29), "29 天")
        self.assertEqual(human_days(30), "1 个月")
        self.assertEqual(human_days(45), "1 个月 15 天")
        self.assertEqual(human_days(365), "1 年")
        self.assertEqual(human_days(400), "1 年 1 个月 5 天")
        self.assertEqual(human_days(-5), "0 天")


class PairingTests(unittest.TestCase):
    def test_single_round_trip(self):
        trades = [
            make_trade(1, "A", BUY, "2024-01-10", 10.0),
            make_trade(2, "A", SELL, "2024-02-10", 12.0),
        ]
        result = pair_trades(trades)
        self.assertEqual(len(result.closed), 1)
        self.assertEqual(result.open_lots, {})
        self.assertEqual(result.unmatched_sells, [])

        pair = result.closed[0]
        self.assertEqual(pair.profit, 2.0)
        self.assertEqual(pair.pct, 20.0)
        self.assertEqual(pair.hold_days, 31)
        self.assertEqual(pair.result, "win")

    def test_fifo_consumes_oldest_buy_first(self):
        trades = [
            make_trade(1, "A", BUY, "2024-01-01", 10.0),
            make_trade(2, "A", BUY, "2024-02-01", 20.0),
            make_trade(3, "A", BUY, "2024-03-01", 30.0),
            make_trade(4, "A", SELL, "2024-04-01", 40.0),
            make_trade(5, "A", SELL, "2024-05-01", 50.0),
        ]
        result = pair_trades(trades)
        self.assertEqual([(p.buy_price, p.sell_price) for p in result.closed],
                         [(10.0, 40.0), (20.0, 50.0)])
        self.assertEqual([lot.buy_price for lot in result.open_lots["A"]], [30.0])

    def test_open_lots_remain_as_position(self):
        trades = [
            make_trade(1, "A", BUY, "2024-01-01", 10.0),
            make_trade(2, "A", BUY, "2024-02-01", 12.0),
            make_trade(3, "A", SELL, "2024-03-01", 15.0),
        ]
        result = pair_trades(trades)
        self.assertEqual(len(result.closed), 1)
        lots = result.open_lots["A"]
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].buy_price, 12.0)

    def test_sell_without_buy_is_unmatched(self):
        trades = [
            make_trade(1, "A", SELL, "2024-01-01", 10.0),
            make_trade(2, "A", SELL, "2024-02-01", 11.0),
            make_trade(3, "A", BUY, "2024-03-01", 9.0),
        ]
        result = pair_trades(trades)
        self.assertEqual(result.closed, [])
        self.assertEqual(len(result.unmatched_sells), 2)
        self.assertEqual([u.sell_price for u in result.unmatched_sells], [10.0, 11.0])
        self.assertEqual(len(result.open_lots["A"]), 1)

    def test_same_date_uses_seq_as_tiebreaker(self):
        trades = [
            make_trade(1, "A", BUY, "2024-01-01", 10.0, seq=2),
            make_trade(2, "A", BUY, "2024-01-01", 20.0, seq=1),
            make_trade(3, "A", SELL, "2024-01-01", 99.0, seq=3),
        ]
        result = pair_trades(trades)
        self.assertEqual(len(result.closed), 1)
        self.assertEqual(result.closed[0].buy_price, 20.0)
        self.assertEqual([lot.buy_price for lot in result.open_lots["A"]], [10.0])

    def test_interleaved_stocks_stay_independent(self):
        trades = [
            make_trade(1, "A", BUY, "2024-01-01", 10.0),
            make_trade(2, "B", BUY, "2024-01-02", 50.0),
            make_trade(3, "A", SELL, "2024-02-01", 11.0),
            make_trade(4, "B", SELL, "2024-03-01", 45.0),
        ]
        result = pair_trades(trades)
        self.assertEqual(len(result.closed), 2)
        by_code = {p.code: p.profit for p in result.closed}
        self.assertEqual(by_code, {"A": 1.0, "B": -5.0})
        self.assertEqual(result.open_lots, {})

    def test_buy_after_sell_keeps_later_buy_open(self):
        trades = [
            make_trade(1, "A", BUY, "2024-01-01", 10.0),
            make_trade(2, "A", SELL, "2024-02-01", 12.0),
            make_trade(3, "A", BUY, "2024-03-01", 9.0),
        ]
        result = pair_trades(trades)
        self.assertEqual(len(result.closed), 1)
        self.assertEqual(result.closed[0].profit, 2.0)
        self.assertEqual(result.open_lots["A"][0].buy_price, 9.0)


class SummaryMathTests(unittest.TestCase):
    def setUp(self):
        self.as_of = date(2024, 6, 30)
        self.trades = [
            make_trade(1, "A", BUY, "2024-01-01", 10.0, name="甲股"),
            make_trade(2, "A", SELL, "2024-01-11", 12.0, name="甲股"),
            make_trade(3, "A", BUY, "2024-02-01", 20.0, name="甲股"),
            make_trade(4, "A", SELL, "2024-02-21", 16.0, name="甲股"),
            make_trade(5, "B", BUY, "2024-03-01", 50.0, name="乙股"),
        ]
        self.summary = build_summary(self.trades, self.as_of)

    def test_totals_match_hand_calculation(self):
        totals = self.summary["totals"]
        # closed pairs: (10 -> 12) = +2, (20 -> 16) = -4
        self.assertEqual(totals["closed_count"], 2)
        self.assertEqual(totals["total_profit"], -2.0)
        self.assertEqual(totals["total_cost"], 30.0)
        self.assertEqual(totals["overall_pct"], -6.67)
        self.assertEqual(totals["buy_count"], 3)
        self.assertEqual(totals["sell_count"], 2)
        self.assertEqual(totals["open_lot_count"], 1)
        self.assertEqual(totals["position_count"], 1)
        self.assertEqual(totals["stock_count"], 2)

    def test_stats_match_hand_calculation(self):
        stats = self.summary["stats"]
        self.assertEqual(stats["win_count"], 1)
        self.assertEqual(stats["loss_count"], 1)
        self.assertEqual(stats["flat_count"], 0)
        self.assertEqual(stats["win_rate"], 50.0)
        self.assertEqual(stats["win_sum"], 2.0)
        self.assertEqual(stats["loss_sum"], -4.0)
        self.assertEqual(stats["profit_factor"], 0.5)
        self.assertEqual(stats["avg_win"], 2.0)
        self.assertEqual(stats["avg_loss"], -4.0)
        self.assertEqual(stats["avg_hold_days"], 15)
        self.assertEqual(stats["max_hold_days"], 20)
        self.assertEqual(stats["min_hold_days"], 10)
        self.assertEqual(stats["best"]["profit"], 2.0)
        self.assertEqual(stats["worst"]["profit"], -4.0)

    def test_position_reports_holding_time(self):
        positions = self.summary["positions"]
        self.assertEqual(len(positions), 1)
        position = positions[0]
        self.assertEqual(position["code"], "B")
        self.assertEqual(position["name"], "乙股")
        self.assertEqual(position["open_count"], 1)
        self.assertEqual(position["cost_price"], 50.0)
        self.assertEqual(position["first_buy_date"], "2024-03-01")
        self.assertEqual(position["holding_days"], 121)
        self.assertEqual(position["holding_text"], "4 个月 1 天")
        self.assertEqual(position["closed_count"], 0)
        self.assertEqual(position["realized_profit"], 0.0)

    def test_per_stock_breakdown(self):
        rows = {row["code"]: row for row in self.summary["per_stock"]}
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows["A"]["closed_count"], 2)
        self.assertEqual(rows["A"]["realized_profit"], -2.0)
        self.assertEqual(rows["A"]["realized_pct"], -6.67)

    def test_no_warnings_when_data_is_consistent(self):
        self.assertEqual(self.summary["warnings"], [])

    def test_unmatched_sell_produces_warning(self):
        trades = self.trades + [make_trade(9, "C", SELL, "2024-04-01", 7.0, name="丙股")]
        summary = build_summary(trades, self.as_of)
        self.assertEqual(summary["totals"]["unmatched_sell_count"], 1)
        self.assertEqual(len(summary["warnings"]), 1)
        self.assertIn("1 笔卖出", summary["warnings"][0])

    def test_empty_input_is_safe(self):
        summary = build_summary([], self.as_of)
        self.assertEqual(summary["totals"]["closed_count"], 0)
        self.assertEqual(summary["totals"]["overall_pct"], 0.0)
        self.assertEqual(summary["stats"]["win_rate"], 0.0)
        self.assertIsNone(summary["stats"]["profit_factor"])
        self.assertIsNone(summary["stats"]["best"])
        self.assertEqual(summary["positions"], [])

    def test_profit_factor_none_without_losses(self):
        trades = [
            make_trade(1, "A", BUY, "2024-01-01", 10.0),
            make_trade(2, "A", SELL, "2024-01-05", 11.0),
        ]
        summary = build_summary(trades, self.as_of)
        self.assertIsNone(summary["stats"]["profit_factor"])
        self.assertEqual(summary["totals"]["overall_pct"], 10.0)

    def test_stock_name_follows_the_latest_trade(self):
        trades = [
            make_trade(1, "A", BUY, "2024-01-01", 10.0, name="旧名"),
            make_trade(2, "A", SELL, "2024-02-01", 12.0, name="新名"),
        ]
        summary = build_summary(trades, self.as_of)
        self.assertEqual(summary["stock_names"], {"A": "新名"})
        self.assertEqual(summary["closed_pairs"][0]["name"], "新名")

    def test_holding_days_per_lot_for_added_position(self):
        trades = [
            make_trade(1, "A", BUY, "2024-01-01", 10.0),
            make_trade(2, "A", SELL, "2024-02-01", 11.0),
            make_trade(3, "A", BUY, "2024-03-01", 12.0),
        ]
        summary = build_summary(trades, self.as_of)
        lot = summary["positions"][0]["lots"][0]
        self.assertEqual(lot["buy_date"], "2024-03-01")
        self.assertEqual(lot["holding_days"], 121)


class CumulativeReturnTests(unittest.TestCase):
    """累计收益 = 每笔平仓收益率连乘后的本金倍数 x 100%。

    1 起始，乘以 (1 + 该笔盈亏率)，结果按百分比展示。
    注意这是"本金倍数"口径：单笔 -10% 会显示为 90%，而非 -10%。
    """

    as_of = date(2024, 12, 31)

    def summary_for(self, legs):
        trades = []
        for index, (code, side, when, price) in enumerate(legs, start=1):
            trades.append(make_trade(index, code, side, when, price))
        return build_summary(trades, self.as_of)

    def test_documented_example(self):
        # +100%, +100%, -50%  ->  1 x 2 x 2 x 0.5 = 2  ->  200%
        summary = self.summary_for([
            ("A", BUY, "2024-01-01", 10.0),
            ("A", SELL, "2024-01-11", 20.0),
            ("B", BUY, "2024-01-01", 10.0),
            ("B", SELL, "2024-01-21", 20.0),
            ("C", BUY, "2024-01-01", 100.0),
            ("C", SELL, "2024-02-01", 50.0),
        ])
        self.assertEqual([p["pct"] for p in summary["closed_pairs"]], [100.0, 100.0, -50.0])
        self.assertEqual(summary["stats"]["cumulative_factor"], 2.0)
        self.assertEqual(summary["stats"]["cumulative_return"], 200.0)

    def test_single_trade_matches_its_own_pct(self):
        summary = self.summary_for([
            ("A", BUY, "2024-01-01", 50.0),
            ("A", SELL, "2024-02-01", 45.0),
        ])
        # -10% -> 0.9 -> 90%
        self.assertEqual(summary["stats"]["cumulative_return"], 90.0)

    def test_no_closed_trades_yields_zero(self):
        summary = self.summary_for([("A", BUY, "2024-01-01", 10.0)])
        self.assertEqual(summary["stats"]["cumulative_return"], 0.0)
        self.assertEqual(summary["stats"]["cumulative_factor"], 0.0)

    def test_compound_differs_from_cost_weighted_average(self):
        # 买10卖12 (+20%), 买100卖95 (-5%)
        summary = self.summary_for([
            ("A", BUY, "2024-01-01", 10.0),
            ("A", SELL, "2024-02-01", 12.0),
            ("B", BUY, "2024-01-01", 100.0),
            ("B", SELL, "2024-03-01", 95.0),
        ])
        # 复利: 1.2 x 0.95 = 1.14 -> 114%
        self.assertEqual(summary["stats"]["cumulative_return"], 114.0)
        # 总盈亏率: (-3)/110 = -2.73%  —— 两者不同
        self.assertEqual(summary["totals"]["overall_pct"], -2.73)

    def test_is_never_negative(self):
        summary = self.summary_for([
            ("A", BUY, "2024-01-01", 100.0),
            ("A", SELL, "2024-02-01", 0.01),
        ])
        self.assertGreater(summary["stats"]["cumulative_return"], 0.0)

    def test_only_closed_trades_compound(self):
        summary = self.summary_for([
            ("A", BUY, "2024-01-01", 10.0),
            ("A", SELL, "2024-02-01", 11.0),
            ("A", BUY, "2024-03-01", 10.0),
        ])
        # 持仓那笔不参与连乘: 1.1 -> 110%
        self.assertEqual(summary["stats"]["cumulative_return"], 110.0)
        self.assertEqual(summary["totals"]["closed_count"], 1)

    def test_multi_buy_fifo_ordering(self):
        summary = self.summary_for([
            ("A", BUY, "2024-01-01", 10.0),
            ("A", BUY, "2024-02-01", 20.0),
            ("A", SELL, "2024-03-01", 40.0),
            ("A", SELL, "2024-04-01", 50.0),
        ])
        # 配对 10->40 (+300%) 与 20->50 (+150%)
        # 1 x 4.0 x 2.5 = 10.0 -> 1000%
        self.assertEqual([p["pct"] for p in summary["closed_pairs"]], [300.0, 150.0])
        self.assertEqual(summary["stats"]["cumulative_return"], 1000.0)


class LoadTradesTests(unittest.TestCase):
    def test_loads_mapping_rows(self):
        rows = [{
            "id": 1, "code": "600519", "name": "贵州茅台", "side": "buy",
            "trade_date": "2024-01-02", "price": 1680.5, "note": "建仓",
            "seq": 1, "created_at": "2024-01-02 10:00:00",
        }]
        trade = core.load_trades(rows)[0]
        self.assertEqual(trade.code, "600519")
        self.assertEqual(trade.side, BUY)
        self.assertEqual(trade.trade_date, date(2024, 1, 2))
        self.assertEqual(trade.price, 1680.5)
        self.assertEqual(trade.sort_key, (date(2024, 1, 2), 1, 1))

    def test_closed_pair_dict_is_json_ready(self):
        pair = ClosedPair(code="A", name="甲", buy_id=1, buy_date=date(2024, 1, 1),
                          buy_price=10.0, buy_note="", sell_id=2,
                          sell_date=date(2024, 1, 31), sell_price=10.5, sell_note="")
        payload = pair.to_dict()
        self.assertEqual(payload["profit"], 0.5)
        self.assertEqual(payload["pct"], 5.0)
        self.assertEqual(payload["hold_days"], 30)
        self.assertEqual(payload["result"], "win")
        self.assertEqual(payload["buy_date"], "2024-01-01")


class ExportTests(unittest.TestCase):
    def sample(self):
        return [
            make_trade(3, "A", SELL, "2024-03-01", 40.0, name="甲股", seq=3),
            make_trade(1, "A", BUY, "2024-01-01", 10.0, name="甲股", seq=1),
            make_trade(2, "A", BUY, "2024-01-01", 20.0, name="甲股", seq=2, note="加仓"),
        ]

    def test_export_is_chronological_and_readable(self):
        payload = core.build_export(self.sample(), "2024-06-30 12:00:00")
        self.assertEqual(payload["format"], core.EXPORT_FORMAT)
        self.assertEqual(payload["version"], core.EXPORT_VERSION)
        self.assertEqual(payload["exported_at"], "2024-06-30 12:00:00")
        self.assertEqual(payload["count"], 3)
        self.assertEqual(
            [row["price"] for row in payload["trades"]], [10.0, 20.0, 40.0]
        )
        self.assertEqual(
            payload["trades"][2],
            {"trade_date": "2024-03-01", "code": "A", "name": "甲股",
             "side": "卖出", "price": 40.0, "note": ""},
        )

    def test_export_hides_internal_ids(self):
        row = core.build_export(self.sample())["trades"][0]
        self.assertNotIn("id", row)
        self.assertNotIn("seq", row)
        self.assertNotIn("created_at", row)

    def test_export_text_is_indented_utf8_json(self):
        text = core.export_text(self.sample(), "2024-06-30 12:00:00")
        self.assertIn('\n  "trades"', text)
        self.assertIn("甲股", text)
        self.assertEqual(json.loads(text)["count"], 3)

    def test_template_round_trips(self):
        result = core.parse_import(json.loads(core.export_template_text()))
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.records[0]["side"], BUY)
        self.assertEqual(result.records[1]["side"], SELL)

    def test_template_trades_pair_into_one_win(self):
        result = core.parse_import(json.loads(core.export_template_text()))
        trades = [
            core.Trade(id=i, code=r["code"], name=r["name"], side=r["side"],
                       trade_date=parse_date(r["trade_date"]), price=r["price"],
                       note=r["note"], seq=i)
            for i, r in enumerate(result.records, start=1)
        ]
        pairing = pair_trades(trades)
        self.assertEqual(len(pairing.closed), 1)
        self.assertEqual(pairing.closed[0].profit, 220.0)


class ImportTests(unittest.TestCase):
    def test_accepts_export_object(self):
        payload = {"format": core.EXPORT_FORMAT, "trades": [
            {"trade_date": "2024-01-02", "code": "600519", "name": "贵州茅台",
             "side": "买入", "price": 1680.5, "note": "建仓"},
        ]}
        result = core.parse_import(payload)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.records, [{
            "code": "600519", "name": "贵州茅台", "side": BUY,
            "trade_date": "2024-01-02", "price": 1680.5, "note": "建仓",
        }])

    def test_accepts_bare_array_and_single_record(self):
        row = {"code": "A", "name": "甲", "side": "sell",
               "date": "2024/1/2", "price": "12"}
        from_array = core.parse_import([row])
        from_object = core.parse_import(row)
        self.assertEqual(from_array.records, from_object.records)
        self.assertEqual(from_array.records[0]["trade_date"], "2024-01-02")
        self.assertEqual(from_array.records[0]["price"], 12.0)

    def test_chinese_and_english_side_both_work(self):
        rows = [
            {"code": "A", "name": "甲", "side": "卖出", "date": "2024-01-02", "price": 1},
            {"code": "A", "name": "甲", "side": "sell", "date": "2024-01-03", "price": 1},
        ]
        self.assertEqual([r["side"] for r in core.parse_import(rows).records],
                         [SELL, SELL])

    def test_name_falls_back_to_code(self):
        result = core.parse_import(
            [{"code": "600519", "side": "buy", "trade_date": "2024-01-02", "price": 10}]
        )
        self.assertEqual(result.errors, [])
        self.assertEqual(result.records[0]["name"], "600519")

    def test_reports_every_bad_row_with_position(self):
        payload = {"trades": [
            {"code": "A", "name": "甲", "side": "buy", "trade_date": "2024-01-02", "price": 10},
            {"code": "B", "name": "乙", "side": "buy", "trade_date": "2024-13-40", "price": 10},
            {"code": "", "name": "丙", "side": "buy", "trade_date": "2024-01-02", "price": 10},
            "不是对象",
            {"code": "D", "name": "丁", "side": "long", "trade_date": "2024-01-02", "price": 10},
        ]}
        result = core.parse_import(payload)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(len(result.errors), 4)
        self.assertTrue(result.errors[0].startswith("第 2 条"))
        self.assertTrue(result.errors[1].startswith("第 3 条"))
        self.assertTrue(result.errors[2].startswith("第 4 条"))
        self.assertTrue(result.errors[3].startswith("第 5 条"))
        self.assertIn("JSON 对象", result.errors[2])

    def test_rejects_unusable_payloads(self):
        for payload in ({}, {"trades": []}, [], "text", 42, {"foo": "bar"}):
            with self.assertRaises(ValidationError, msg=repr(payload)):
                core.parse_import(payload)

    def test_rejects_oversized_payload(self):
        rows = [{"code": "A", "name": "甲", "side": "buy",
                 "trade_date": "2024-01-02", "price": 10}] * (core.MAX_IMPORT_RECORDS + 1)
        with self.assertRaises(ValidationError):
            core.parse_import(rows)


class WarningTests(unittest.TestCase):
    def test_no_warning_without_unmatched_sells(self):
        pairing = core.pair_trades([
            make_trade(1, "A", BUY, "2024-01-01", 10.0),
            make_trade(2, "A", SELL, "2024-02-01", 11.0),
        ])
        self.assertEqual(core.collect_warnings(pairing), [])

    def test_warning_counts_unmatched_sells(self):
        pairing = core.pair_trades([
            make_trade(1, "A", SELL, "2024-01-01", 10.0),
            make_trade(2, "A", SELL, "2024-01-02", 10.0),
        ])
        warnings = core.collect_warnings(pairing)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 笔卖出", warnings[0])
        self.assertIn("A", warnings[0])

    def test_warning_names_every_affected_stock(self):
        pairing = core.pair_trades([
            make_trade(1, "B", SELL, "2024-01-01", 10.0),
            make_trade(2, "A", SELL, "2024-01-01", 10.0),
        ])
        warning = core.collect_warnings(pairing)[0]
        self.assertIn("2 笔卖出", warning)
        self.assertIn("A、B", warning)

    def test_sell_without_any_buy_is_also_reported(self):
        # 卖出数多于买入数：第一次配对后剩下的卖出同样无法配对
        pairing = core.pair_trades([
            make_trade(1, "A", BUY, "2024-01-01", 10.0),
            make_trade(2, "A", SELL, "2024-02-01", 11.0),
            make_trade(3, "A", SELL, "2024-03-01", 12.0),
        ])
        warnings = core.collect_warnings(pairing)
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 笔卖出", warnings[0])
        self.assertIn("卖出多于买入", warnings[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)

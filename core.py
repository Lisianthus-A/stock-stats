from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

BUY = "buy"
SELL = "sell"

DATE_FMT = "%Y-%m-%d"
MONEY_DP = 4
PCT_DP = 2
MAX_CODE_LEN = 20
MAX_NAME_LEN = 40
MAX_NOTE_LEN = 200
MAX_PRICE = 1_000_000_000.0

EXPORT_FORMAT = "stock-stats/trades"
EXPORT_VERSION = 1
EXPORT_NOTE = (
    "side 可写 买入/卖出 或 buy/sell；日期 YYYY-MM-DD；"
    "同一天的多笔按本文件中的先后顺序配对"
)
MAX_IMPORT_RECORDS = 20_000

_SIDE_ALIASES = {
    "buy": BUY, "b": BUY, "1": BUY,
    "买": BUY, "买入": BUY, "建仓": BUY,
    "sell": SELL, "s": SELL, "0": SELL, "2": SELL,
    "卖": SELL, "卖出": SELL, "清仓": SELL,
}

_SIDE_LABELS = {BUY: "买入", SELL: "卖出"}

# 导入时允许的字段别名，方便手工改 JSON（date / 日期 等常见写法都能认）
_FIELD_ALIASES = {
    "code": ("code", "symbol", "ticker", "代码", "股票代码"),
    "name": ("name", "名称", "股票名称"),
    "side": ("side", "type", "direction", "类型", "方向", "交易类型"),
    "trade_date": ("trade_date", "date", "日期", "交易日期"),
    "price": ("price", "价格", "成交价"),
    "note": ("note", "remark", "备注"),
}

_DATE_RE = re.compile(r"(\d{4})\D{0,2}(\d{1,2})\D{0,2}(\d{1,2})")


class ValidationError(ValueError):
    pass


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValidationError("日期不能为空")
    match = _DATE_RE.fullmatch(text)
    if not match:
        raise ValidationError(f"日期格式错误：{text}（应为 YYYY-MM-DD）")
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        raise ValidationError(f"日期不存在：{text}") from None


def parse_price(value: Any) -> float:
    try:
        price = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"价格不是有效数字：{value!r}") from None
    if price != price or price in (float("inf"), float("-inf")):
        raise ValidationError("价格不是有效数字")
    if price <= 0:
        raise ValidationError("价格必须大于 0")
    if price > MAX_PRICE:
        raise ValidationError("价格超出合理范围")
    return round(price, MONEY_DP)


def normalize_side(value: Any) -> str:
    side = _SIDE_ALIASES.get(str(value or "").strip().lower())
    if side is None:
        raise ValidationError("交易类型必须是买入或卖出")
    return side


def normalize_trade(payload: Mapping[str, Any]) -> dict:
    if not isinstance(payload, Mapping):
        raise ValidationError("交易数据格式错误")

    code = str(payload.get("code") or "").strip()
    if not code:
        raise ValidationError("股票代码不能为空")
    if len(code) > MAX_CODE_LEN:
        raise ValidationError(f"股票代码最多 {MAX_CODE_LEN} 个字符")

    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValidationError("股票名称不能为空")
    if len(name) > MAX_NAME_LEN:
        raise ValidationError(f"股票名称最多 {MAX_NAME_LEN} 个字符")

    trade_date = parse_date(payload.get("trade_date"))
    price = parse_price(payload.get("price"))

    note = str(payload.get("note") or "").strip()
    if len(note) > MAX_NOTE_LEN:
        raise ValidationError(f"备注最多 {MAX_NOTE_LEN} 个字符")

    return {
        "code": code,
        "name": name,
        "side": normalize_side(payload.get("side")),
        "trade_date": trade_date.strftime(DATE_FMT),
        "price": price,
        "note": note,
    }


def human_days(days: int) -> str:
    if days < 0:
        days = 0
    if days < 30:
        return f"{days} 天"
    if days < 365:
        months, rest = divmod(days, 30)
        return f"{months} 个月" if rest == 0 else f"{months} 个月 {rest} 天"
    years, remainder = divmod(days, 365)
    months, rest = divmod(remainder, 30)
    parts = [f"{years} 年"]
    if months:
        parts.append(f"{months} 个月")
    if rest:
        parts.append(f"{rest} 天")
    return " ".join(parts)


def _pct(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100.0, PCT_DP)


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), MONEY_DP)


def result_label(profit: float) -> str:
    if profit > 0:
        return "win"
    if profit < 0:
        return "loss"
    return "flat"


@dataclass(frozen=True)
class Trade:
    code: str
    name: str
    side: str
    trade_date: date
    price: float
    id: int = 0
    note: str = ""
    seq: int = 0
    created_at: str = ""

    @property
    def sort_key(self) -> tuple:
        return (self.trade_date, self.seq, self.id)


@dataclass(frozen=True)
class ClosedPair:
    code: str
    name: str
    buy_id: int
    buy_date: date
    buy_price: float
    buy_note: str
    sell_id: int
    sell_date: date
    sell_price: float
    sell_note: str

    @property
    def profit(self) -> float:
        return round(self.sell_price - self.buy_price, MONEY_DP)

    @property
    def pct(self) -> float:
        return _pct(self.sell_price - self.buy_price, self.buy_price)

    @property
    def hold_days(self) -> int:
        return (self.sell_date - self.buy_date).days

    @property
    def result(self) -> str:
        return result_label(self.profit)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "buy_id": self.buy_id,
            "buy_date": self.buy_date.strftime(DATE_FMT),
            "buy_price": self.buy_price,
            "buy_note": self.buy_note,
            "sell_id": self.sell_id,
            "sell_date": self.sell_date.strftime(DATE_FMT),
            "sell_price": self.sell_price,
            "sell_note": self.sell_note,
            "profit": self.profit,
            "pct": self.pct,
            "hold_days": self.hold_days,
            "hold_text": human_days(self.hold_days),
            "result": self.result,
        }


@dataclass(frozen=True)
class OpenLot:
    buy_id: int
    buy_date: date
    buy_price: float
    note: str
    seq: int = 0

    def holding_days(self, as_of: date) -> int:
        return max(0, (as_of - self.buy_date).days)

    def to_dict(self, as_of: date) -> dict:
        days = self.holding_days(as_of)
        return {
            "id": self.buy_id,
            "buy_date": self.buy_date.strftime(DATE_FMT),
            "buy_price": self.buy_price,
            "note": self.note,
            "holding_days": days,
            "holding_text": human_days(days),
        }


@dataclass(frozen=True)
class UnmatchedSell:
    sell_id: int
    code: str
    name: str
    sell_date: date
    sell_price: float
    note: str

    def to_dict(self) -> dict:
        return {
            "id": self.sell_id,
            "code": self.code,
            "name": self.name,
            "sell_date": self.sell_date.strftime(DATE_FMT),
            "sell_price": self.sell_price,
            "note": self.note,
        }


@dataclass(frozen=True)
class Pairing:
    """一次 FIFO 配对的结果。列表均已按交易先后排好序，可直接使用。"""

    trades_by_code: dict[str, list[Trade]]
    closed: list[ClosedPair]
    closed_by_code: dict[str, list[ClosedPair]]
    open_lots: dict[str, list[OpenLot]]
    unmatched_sells: list[UnmatchedSell]

    @property
    def stock_names(self) -> dict[str, str]:
        """每只股票的最新名称（按交易时间取最后一次出现的名字）。"""
        return {code: items[-1].name for code, items in self.trades_by_code.items()}


def trade_from_mapping(row: Mapping[str, Any]) -> Trade:
    return Trade(
        id=int(row["id"] or 0),
        code=str(row["code"]).strip(),
        name=str(row["name"]).strip(),
        side=normalize_side(row["side"]),
        trade_date=parse_date(row["trade_date"]),
        price=round(float(row["price"]), MONEY_DP),
        note=str(row["note"] or "").strip(),
        seq=int(row["seq"] or 0),
        created_at=str(row["created_at"] or ""),
    )


def load_trades(rows: Iterable[Mapping[str, Any]]) -> list[Trade]:
    return [trade_from_mapping(row) for row in rows]


def trade_to_dict(trade: Trade) -> dict:
    return {
        "id": trade.id,
        "code": trade.code,
        "name": trade.name,
        "side": trade.side,
        "trade_date": trade.trade_date.strftime(DATE_FMT),
        "price": trade.price,
        "note": trade.note,
        "seq": trade.seq,
        "created_at": trade.created_at,
    }


def collect_warnings(pairing: Pairing) -> list[str]:
    if not pairing.unmatched_sells:
        return []
    codes = "、".join(sorted({item.code for item in pairing.unmatched_sells}))
    return [
        f"有 {len(pairing.unmatched_sells)} 笔卖出（{codes}）无法配对，未计入盈亏统计："
        "卖出多于买入，或卖出日期早于该股票最早的买入记录，请检查日期或补录买入记录。"
    ]


def trade_to_export(trade: Trade) -> dict:
    return {
        "trade_date": trade.trade_date.strftime(DATE_FMT),
        "code": trade.code,
        "name": trade.name,
        "side": _SIDE_LABELS[trade.side],
        "price": trade.price,
        "note": trade.note,
    }


def build_export(trades: Iterable[Trade], exported_at: str | None = None) -> dict:
    ordered = sorted(trades, key=lambda t: t.sort_key)
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": exported_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(ordered),
        "note": EXPORT_NOTE,
        "trades": [trade_to_export(t) for t in ordered],
    }


def export_text(trades: Iterable[Trade], exported_at: str | None = None) -> str:
    payload = build_export(trades, exported_at)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


TEMPLATE_TRADES: tuple[Trade, ...] = (
    Trade(
        id=0,
        code="600519",
        name="贵州茅台",
        side=BUY,
        trade_date=date(2024, 1, 2),
        price=1680.0,
        note="建仓",
        seq=1,
    ),
    Trade(
        id=0,
        code="600519",
        name="贵州茅台",
        side=SELL,
        trade_date=date(2024, 3, 8),
        price=1900.0,
        note="止盈",
        seq=2,
    ),
)


def export_template_text() -> str:
    return export_text(TEMPLATE_TRADES, "示例数据（非真实交易，请改成自己的数据）")


def _field_value(row: Mapping[str, Any], field: str) -> Any:
    for alias in _FIELD_ALIASES[field]:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return None


def normalize_import_trade(row: Mapping[str, Any]) -> dict:
    payload = {field: _field_value(row, field) for field in _FIELD_ALIASES}
    if not payload["name"]:
        payload["name"] = payload["code"]
    return normalize_trade(payload)


def extract_import_rows(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in ("trades", "records", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        known = {alias for aliases in _FIELD_ALIASES.values() for alias in aliases}
        if known & set(payload):
            return [payload]
    raise ValidationError(
        "无法识别数据格式：需要一个交易记录数组，或包含 trades 数组的对象"
    )


@dataclass(frozen=True)
class ImportResult:
    records: list[dict]
    errors: list[str]


def parse_import(payload: Any) -> ImportResult:
    """解析导入内容：逐条校验，把所有出错行一次性列出来。

    只要有一行不合法就不该落库（errors 非空时由调用方拒绝整个导入），
    否则用户会得到一份「导入了一半」的流水。
    """
    rows = extract_import_rows(payload)
    if not rows:
        raise ValidationError("导入内容为空：没有找到任何交易记录")
    if len(rows) > MAX_IMPORT_RECORDS:
        raise ValidationError(
            f"一次最多导入 {MAX_IMPORT_RECORDS} 条记录，当前 {len(rows)} 条"
        )

    records: list[dict] = []
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        try:
            if not isinstance(row, Mapping):
                raise ValidationError("记录必须是 JSON 对象")
            records.append(normalize_import_trade(row))
        except ValidationError as exc:
            errors.append(f"第 {index} 条：{exc}")
    return ImportResult(records=records, errors=errors)


def pair_trades(trades: Iterable[Trade]) -> Pairing:
    grouped: dict[str, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(trade.code, []).append(trade)
    for items in grouped.values():
        items.sort(key=lambda t: t.sort_key)

    closed: list[ClosedPair] = []
    open_lots: dict[str, list[OpenLot]] = {}
    unmatched: list[UnmatchedSell] = []

    for code, items in sorted(grouped.items()):
        queue: deque[OpenLot] = deque()

        for trade in items:
            if trade.side == BUY:
                queue.append(
                    OpenLot(
                        buy_id=trade.id,
                        buy_date=trade.trade_date,
                        buy_price=trade.price,
                        note=trade.note,
                        seq=trade.seq,
                    )
                )
            elif queue:
                lot = queue.popleft()
                closed.append(
                    ClosedPair(
                        code=code,
                        name=trade.name or code,
                        buy_id=lot.buy_id,
                        buy_date=lot.buy_date,
                        buy_price=lot.buy_price,
                        buy_note=lot.note,
                        sell_id=trade.id,
                        sell_date=trade.trade_date,
                        sell_price=trade.price,
                        sell_note=trade.note,
                    )
                )
            else:
                unmatched.append(
                    UnmatchedSell(
                        sell_id=trade.id,
                        code=code,
                        name=trade.name,
                        sell_date=trade.trade_date,
                        sell_price=trade.price,
                        note=trade.note,
                    )
                )

        if queue:
            open_lots[code] = list(queue)

    closed.sort(key=lambda pair: (pair.sell_date, pair.sell_id))
    unmatched.sort(key=lambda item: (item.sell_date, item.sell_id))
    closed_by_code: dict[str, list[ClosedPair]] = {}
    for pair in closed:
        closed_by_code.setdefault(pair.code, []).append(pair)
    return Pairing(
        trades_by_code=grouped,
        closed=closed,
        closed_by_code=closed_by_code,
        open_lots=open_lots,
        unmatched_sells=unmatched,
    )


def build_positions(pairing: Pairing, as_of: date) -> list[dict]:
    positions: list[dict] = []
    for code, lots in pairing.open_lots.items():
        stock_trades = pairing.trades_by_code[code]
        pairs = pairing.closed_by_code.get(code, [])
        realized_profit = round(sum(p.profit for p in pairs), MONEY_DP)
        realized_cost = round(sum(p.buy_price for p in pairs), MONEY_DP)
        first_buy_date = min(lot.buy_date for lot in lots)
        holding_days = max(0, (as_of - first_buy_date).days)
        lot_dicts = [lot.to_dict(as_of) for lot in lots]
        last_lot = lot_dicts[-1]
        positions.append(
            {
                "code": code,
                "name": stock_trades[-1].name,
                "open_count": len(lot_dicts),
                "cost_price": last_lot["buy_price"],
                "avg_cost": _mean([lot["buy_price"] for lot in lot_dicts]),
                "first_buy_date": first_buy_date.strftime(DATE_FMT),
                "holding_days": holding_days,
                "holding_text": human_days(holding_days),
                "closed_count": len(pairs),
                "realized_profit": realized_profit,
                "realized_pct": _pct(realized_profit, realized_cost),
                "lots": lot_dicts,
                "trades": [trade_to_dict(t) for t in stock_trades],
            }
        )

    positions.sort(key=lambda p: (p["first_buy_date"], p["code"]), reverse=True)
    return positions


def cumulative_factor(closed: Sequence[ClosedPair]) -> float:
    factor = 1.0
    for pair in closed:
        factor *= 1.0 + (pair.sell_price - pair.buy_price) / pair.buy_price
    return factor


def build_stats(closed: Sequence[ClosedPair]) -> dict:
    wins = [p for p in closed if p.profit > 0]
    losses = [p for p in closed if p.profit < 0]
    win_sum = round(sum(p.profit for p in wins), MONEY_DP)
    loss_sum = round(sum(p.profit for p in losses), MONEY_DP)
    hold_days = [p.hold_days for p in closed]
    avg_hold_days = int(round(sum(hold_days) / len(hold_days))) if hold_days else 0
    profit_factor = round(win_sum / abs(loss_sum), 2) if loss_sum else None
    factor = cumulative_factor(closed) if closed else 0.0

    def extreme(pool: Sequence[ClosedPair], pick) -> dict | None:
        if not pool:
            return None
        return pick(pool, key=lambda p: p.profit).to_dict()

    return {
        "win_count": len(wins),
        "loss_count": len(losses),
        "flat_count": len(closed) - len(wins) - len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, PCT_DP) if closed else 0.0,
        "profit_factor": profit_factor,
        "avg_win": _mean([p.profit for p in wins]),
        "avg_loss": _mean([p.profit for p in losses]),
        "avg_profit": _mean([p.profit for p in closed]),
        "avg_pct": _mean([p.pct for p in closed]),
        "cumulative_factor": round(factor, 6),
        "cumulative_return": round(factor * 100.0, PCT_DP),
        "avg_hold_days": avg_hold_days,
        "avg_hold_text": human_days(avg_hold_days),
        "max_hold_days": max(hold_days, default=0),
        "min_hold_days": min(hold_days, default=0),
        "win_sum": win_sum,
        "loss_sum": loss_sum,
        "best": extreme(closed, max),
        "worst": extreme(closed, min),
    }


def build_summary(trades: Sequence[Trade], as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    pairing = pair_trades(trades)
    closed = pairing.closed
    stock_names = pairing.stock_names

    total_profit = round(sum(p.profit for p in closed), MONEY_DP)
    total_cost = round(sum(p.buy_price for p in closed), MONEY_DP)
    open_lot_count = sum(len(lots) for lots in pairing.open_lots.values())

    per_stock: list[dict] = []
    for code, pairs in pairing.closed_by_code.items():
        profit = round(sum(p.profit for p in pairs), MONEY_DP)
        cost = round(sum(p.buy_price for p in pairs), MONEY_DP)
        per_stock.append(
            {
                "code": code,
                "name": stock_names.get(code, code),
                "closed_count": len(pairs),
                "realized_profit": profit,
                "realized_pct": _pct(profit, cost),
                "avg_hold_days": int(round(_mean([float(p.hold_days) for p in pairs]))),
            }
        )
    per_stock.sort(key=lambda item: item["realized_profit"], reverse=True)

    return {
        "as_of": as_of.strftime(DATE_FMT),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totals": {
            "trade_count": len(trades),
            "buy_count": sum(1 for t in trades if t.side == BUY),
            "sell_count": sum(1 for t in trades if t.side == SELL),
            "closed_count": len(closed),
            "open_lot_count": open_lot_count,
            "position_count": len(pairing.open_lots),
            "stock_count": len(stock_names),
            "unmatched_sell_count": len(pairing.unmatched_sells),
            "total_profit": total_profit,
            "total_cost": total_cost,
            "overall_pct": _pct(total_profit, total_cost),
        },
        "stats": build_stats(closed),
        "positions": build_positions(pairing, as_of),
        "closed_pairs": [p.to_dict() for p in closed],
        "unmatched_sells": [u.to_dict() for u in pairing.unmatched_sells],
        "per_stock": per_stock,
        "stock_names": stock_names,
        "warnings": collect_warnings(pairing),
    }

"""Exact order totals with a strict candidate mapping boundary."""
import json
import re
import sys


def summarize(data):
    if not isinstance(data, dict) or set(data) != {"orders", "mapping"}:
        raise ValueError("需要 orders 和 mapping")
    mapping = data["mapping"]
    if not isinstance(mapping, dict) or set(mapping) != {"amount", "status"}:
        raise ValueError("候选映射必须包含 amount/status")
    if any(not isinstance(v, str) or not v for v in mapping.values()) or len(set(mapping.values())) != 2:
        raise ValueError("映射字段必须是非空且互异的字符串")
    if not isinstance(data["orders"], list):
        raise ValueError("orders 必须是数组")
    total_cents = 0
    count = 0
    for row in data["orders"]:
        if not isinstance(row, dict) or any(v not in row for v in mapping.values()):
            raise ValueError("订单缺少映射字段")
        amount = row[mapping["amount"]]
        status = row[mapping["status"]]
        if status not in ("paid", "cancelled"):
            raise ValueError("未知订单状态")
        if not isinstance(amount, str):
            raise ValueError("金额必须为十进制字符串")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", amount):
            raise ValueError("金额必须为非负、最多两位小数")
        whole, _, fraction = amount.partition(".")
        cents = int(whole) * 100 + int((fraction + "00")[:2])
        if status == "paid":
            total_cents += cents
            count += 1
    return {"paid_count": count, "total": "%d.%02d" % divmod(total_cents, 100)}


if __name__ == "__main__":
    try:
        print(json.dumps(summarize(json.load(sys.stdin)), ensure_ascii=False))
    except (ValueError, TypeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))

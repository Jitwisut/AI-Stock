"""Backtest ของคะแนนเทคนิค — ตรวจว่าคะแนนสูงมีผลตอบแทนหลังจากนั้นดีกว่าจริงไหม.

การป้องกัน bias
- Look-ahead: indicator ณ วัน t คำนวณจากแท่ง <= t เท่านั้น (TechnicalSeries เป็น causal)
  และเข้าซื้อที่ราคาเปิดของวัน t+1 (ไม่ใช่ราคาปิดวัน t ที่ใช้คำนวณสัญญาณ)
- Future leakage: ไม่ใช้ข้อมูลพื้นฐาน/ข่าว/วันประกาศงบ เพราะ provider ไม่มีข้อมูลย้อนหลังแบบ
  point-in-time — backtest นี้จึงวัดเฉพาะส่วนเทคนิคของคะแนน (Trend/Momentum/Volume/Risk)
- Survivorship bias: รายชื่อหุ้นมาจากหุ้นที่ยังจดทะเบียนอยู่วันนี้ (ไม่มีรายชื่อย้อนหลัง
  ของหุ้นที่ถูกถอน) → ผลตอบแทนมีแนวโน้ม "ดีเกินจริง" ต้องอ่านแบบเปรียบเทียบระหว่างกลุ่มคะแนน
- ช่วงเวลาถือซ้อนกัน (step < horizon) → ตัวอย่างไม่อิสระต่อกัน จำนวน n ดูมากเกินจริง
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from workers.engine import analysis as an
from workers.engine import store
from workers.engine.bars import get_daily_bars
from workers.engine.runtime import iso, utcnow
from workers.engine.stock import benchmark_closes

logger = logging.getLogger(__name__)

HORIZONS = (1, 5, 20, 60)
BUCKETS = ((80, 101, "≥80"), (70, 80, "70-80"), (60, 70, "60-70"), (50, 60, "50-60"), (0, 50, "<50"))
MIN_HISTORY = 252


def technical_score(t: dict) -> float:
    comps = {
        "trend": an.score_trend(t),
        "momentum": an.score_momentum(t),
        "volume": an.score_volume(t),
        "risk": {"score": an.assess_risk(t)["score"], "max": an.COMPONENT_MAX["risk"]},
    }
    return an.weighted_score(comps, an.COMPONENT_MAX, technical_only=True)["total"]


def samples_for_symbol(bars, bench: dict, step: int) -> list[dict]:
    ts = an.TechnicalSeries.build(bars)
    out = []
    last_index = len(bars) - 2  # ต้องมีแท่ง t+1 สำหรับเข้าซื้อ
    for i in range(MIN_HISTORY, last_index + 1, step):
        t = ts.snapshot_at(i, bench)
        score = technical_score(t)
        entry_bar = bars[i + 1]
        entry = entry_bar.open or entry_bar.close
        if not entry or entry <= 0:
            continue
        row = {"date": bars[i].day.isoformat(), "score": score}
        for h in HORIZONS:
            exit_index = i + h
            if exit_index >= len(bars):
                row[f"r{h}"] = None
                continue
            row[f"r{h}"] = (bars[exit_index].close / entry - 1) * 100
            lows = [b.low if b.low is not None else b.close for b in bars[i + 1 : exit_index + 1]]
            row[f"mae{h}"] = (min(lows) / entry - 1) * 100
            spy_now, spy_then = bench.get(bars[exit_index].day), bench.get(bars[i].day)
            row[f"spy{h}"] = (spy_now / spy_then - 1) * 100 if spy_now and spy_then else None
        out.append(row)
    return out


def _stats(rows: list[dict], h: int) -> dict:
    returns = [r[f"r{h}"] for r in rows if r.get(f"r{h}") is not None]
    if not returns:
        return {"n": 0}
    maes = [r[f"mae{h}"] for r in rows if r.get(f"mae{h}") is not None]
    excess = [r[f"r{h}"] - r[f"spy{h}"] for r in rows if r.get(f"r{h}") is not None and r.get(f"spy{h}") is not None]
    return {
        "n": len(returns),
        "win_rate_percent": round(sum(1 for x in returns if x > 0) / len(returns) * 100, 1),
        "avg_return_percent": round(statistics.fmean(returns), 2),
        "median_return_percent": round(statistics.median(returns), 2),
        "avg_excess_vs_spy_percent": round(statistics.fmean(excess), 2) if excess else None,
        "avg_max_drawdown_percent": round(statistics.fmean(maes), 2) if maes else None,
        "worst_drawdown_percent": round(min(maes), 2) if maes else None,
    }


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 30:
        return None

    def ranks(values):
        order = sorted(range(len(values)), key=lambda k: values[k])
        result = [0.0] * len(values)
        for rank, index in enumerate(order):
            result[index] = float(rank)
        return result

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return round(cov / (vx * vy), 4) if vx and vy else None


def run_backtest(params: dict, progress=lambda m: None) -> dict:
    from workers.engine.discovery import build_universe, select_deep_scan

    started = time.perf_counter()
    sample = int(params.get("sample", 150))
    step = int(params.get("step", 5))
    universe, _stats_universe = build_universe({"min_price": 2.0, "min_market_cap": 300e6})
    symbols = [x["symbol"] for x in select_deep_scan(universe, sample)]
    bench = benchmark_closes()
    rows: list[dict] = []
    failed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(get_daily_bars, symbol): symbol for symbol in symbols}
        for n, future in enumerate(as_completed(futures), start=1):
            try:
                series = future.result()
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            if len(series.bars) < MIN_HISTORY + 30:
                continue
            for row in samples_for_symbol(series.bars, bench, step):
                row["symbol"] = futures[future]
                rows.append(row)
            if n % 20 == 0:
                progress(f"ประมวลผลแล้ว {n}/{len(symbols)} ตัว")

    results = {}
    for h in HORIZONS:
        buckets = {}
        for low, high, label in BUCKETS:
            buckets[label] = _stats([r for r in rows if low <= r["score"] < high], h)
        valid = [r for r in rows if r.get(f"r{h}") is not None]
        results[f"{h}D"] = {
            "all": _stats(rows, h),
            "by_score_bucket": buckets,
            "spearman_score_vs_return": _spearman([r["score"] for r in valid], [r[f"r{h}"] for r in valid]),
        }
    dates = sorted(r["date"] for r in rows)
    return {
        "method": {
            "score": "technical-only Opportunity Score (Trend/Momentum/Volume/Risk, reweighted to 100)",
            "entry": "next session open after the signal date",
            "exit": "close after N sessions",
            "drawdown": "max adverse excursion: lowest low between entry and exit vs entry",
            "sampling": f"every {step} sessions per symbol, after {MIN_HISTORY} sessions of warm-up",
        },
        "universe": {"symbols_requested": len(symbols), "symbols_failed": failed, "selection": "today's liquid US stocks (Nasdaq screener)"},
        "period": {"from": dates[0] if dates else None, "to": dates[-1] if dates else None, "observations": len(rows)},
        "horizons": results,
        "bias_warnings": [
            "Survivorship bias: ใช้เฉพาะหุ้นที่ยังจดทะเบียนและมีสภาพคล่องสูง ณ วันนี้ — ผลตอบแทนมีแนวโน้มสูงเกินจริง",
            "ไม่รวมคะแนน Fundamental/Catalyst เพราะไม่มีข้อมูลย้อนหลังแบบ point-in-time (กัน future leakage)",
            "ช่วงถือซ้อนกันเมื่อ step < horizon ทำให้ตัวอย่างไม่อิสระต่อกัน",
            "ช่วงเวลาทดสอบสั้น (~1 ปี) และเป็นช่วงตลาดเดียว — ยังสรุปเป็นกฎทั่วไปไม่ได้",
            "ไม่รวมค่าธรรมเนียมและ slippage",
        ],
        "duration_seconds": round(time.perf_counter() - started, 1),
    }


_STATUS: dict = {}
_LOCK = threading.Lock()


def start_backtest(params: dict) -> dict:
    with _LOCK:
        for status in _STATUS.values():
            if status["status"] in {"queued", "running"}:
                # เธรดที่ตายกลางทาง (เช่น OOM) จะค้างสถานะ running — เกิน 30 นาทีถือว่าล้มเหลว
                if time.time() - status.get("_started_ts", time.time()) > 30 * 60:
                    status.update(status="failed", error="interrupted: backtest did not finish")
                    continue
                return {k: v for k, v in status.items() if k not in {"result", "_started_ts"}}
        for key in [k for k, v in _STATUS.items() if v["status"] in {"completed", "failed"}][:-3]:
            _STATUS.pop(key, None)
        run_id = store.insert_run("backtest_runs", {"status": "queued", "params": params}) or f"mem-{int(time.time())}"
        status = {"id": run_id, "status": "queued", "params": params, "started_at": iso(utcnow()), "progress": "รอเริ่ม", "_started_ts": time.time()}
        _STATUS[run_id] = status

    def _run():
        status["status"] = "running"
        store.update_run("backtest_runs", run_id if isinstance(run_id, int) else None, {"status": "running"})
        try:
            result = run_backtest(params, lambda m: status.update(progress=m))
            status.update(status="completed", result=result, finished_at=iso(utcnow()), progress="เสร็จ")
            store.update_run("backtest_runs", run_id if isinstance(run_id, int) else None,
                             {"status": "completed", "results": result, "finished_at": utcnow()})
        except Exception as exc:  # noqa: BLE001
            logger.warning("backtest_failed", extra={"event": "backtest_failed"}, exc_info=True)
            status.update(status="failed", error=str(exc)[:300], finished_at=iso(utcnow()))
            store.update_run("backtest_runs", run_id if isinstance(run_id, int) else None,
                             {"status": "failed", "error": str(exc)[:300], "finished_at": utcnow()})

    threading.Thread(target=_run, name="backtest", daemon=True).start()
    return {k: v for k, v in status.items() if k not in {"result", "_started_ts"}}


def get_backtest(run_id) -> dict | None:
    key = int(run_id) if isinstance(run_id, str) and run_id.isdigit() else run_id
    if key in _STATUS:
        return {k: v for k, v in _STATUS[key].items() if k != "_started_ts"}
    if isinstance(key, int):
        row = store.fetch_run("backtest_runs", run_id=key)
        if row:
            return {"id": row["id"], "status": row["status"], "result": row.get("results"), "error": row.get("error"),
                    "started_at": row["started_at"], "finished_at": row.get("finished_at")}
    return None


def latest_backtest() -> dict | None:
    done = [s for s in _STATUS.values() if s["status"] == "completed"]
    if done:
        return {k: v for k, v in done[-1].items() if k != "_started_ts"}
    row = store.fetch_run("backtest_runs", where={"status": "completed"})
    if row:
        return {"id": row["id"], "status": "completed", "result": row.get("results"), "finished_at": row.get("finished_at")}
    return None

"use client";

import { AlertTriangle, CheckCircle2, ListOrdered, RefreshCw, Search, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const PAGE_SIZE = 100;

type Stock = {
  symbol: string;
  name: string;
  exchange: string;
  sector?: string | null;
  market_cap?: number | null;
  is_active: boolean;
};

export default function StocksPage() {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [query, setQuery] = useState("");
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setError("");
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/stocks`, { cache: "no-store" });
      if (!response.ok) throw new Error("เรียกรายชื่อหุ้นไม่สำเร็จ");
      setStocks((await response.json()) as Stock[]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลดไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const filtered = useMemo(() => {
    const normalized = query.trim().toUpperCase();
    if (!normalized) return stocks;
    return stocks.filter(
      (stock) =>
        stock.symbol.includes(normalized) ||
        stock.name.toUpperCase().includes(normalized) ||
        stock.exchange.toUpperCase().includes(normalized)
    );
  }, [query, stocks]);

  useEffect(() => {
    setVisible(PAGE_SIZE);
  }, [query]);

  const shown = filtered.slice(0, visible);

  return (
    <main className="ambient relative mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-ink-subtle">
            รายชื่อ
          </p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
            หุ้นทั้งหมดในตลาด
          </h1>
          <p className="mt-1.5 text-sm leading-6 text-ink-muted">
            {loading
              ? "กำลังโหลดรายชื่อหุ้น..."
              : `พบ ${stocks.length.toLocaleString()} ตัว · แสดง ${shown.length.toLocaleString()} จาก ${filtered.length.toLocaleString()}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex h-9 w-full min-w-0 items-center gap-2 rounded-lg border border-bg-line bg-bg-base/60 px-3 sm:w-72">
            <Search className="h-3.5 w-3.5 text-ink-subtle" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="ค้นหา symbol หรือชื่อบริษัท..."
              className="w-full bg-transparent text-sm text-ink placeholder:text-ink-subtle outline-none"
            />
          </label>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="focus-ring inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-lg border border-bg-line bg-bg-raised text-ink-muted transition hover:text-ink disabled:opacity-60"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </header>

      {error ? (
        <div className="mb-5 flex items-center gap-2 rounded-xl border border-loss/30 bg-loss/10 px-4 py-3 text-sm text-loss">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      ) : null}

      <section className="panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-left text-sm">
            <thead className="sticky top-0 z-10 bg-bg-surface/95 text-[11px] uppercase tracking-wider text-ink-subtle backdrop-blur">
              <tr>
                <th className="px-4 py-3 font-medium">Symbol</th>
                <th className="px-4 py-3 font-medium">ชื่อบริษัท</th>
                <th className="px-4 py-3 font-medium">ตลาด</th>
                <th className="px-4 py-3 font-medium">หมวด</th>
                <th className="px-4 py-3 font-medium">สถานะ</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-ink-subtle">
                    กำลังโหลด...
                  </td>
                </tr>
              ) : shown.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-ink-subtle">
                    ไม่พบหุ้นที่ตรงกับการค้นหา
                  </td>
                </tr>
              ) : (
                shown.map((stock) => (
                  <tr key={stock.symbol} className="border-t border-bg-line transition hover:bg-bg-raised/50">
                    <td className="px-4 py-2.5 font-mono text-sm font-semibold text-ink">
                      {stock.symbol}
                    </td>
                    <td className="px-4 py-2.5 text-ink-muted">{stock.name}</td>
                    <td className="px-4 py-2.5">
                      <span className="chip text-ink-muted">{stock.exchange}</span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-ink-subtle">{stock.sector ?? "—"}</td>
                    <td className="px-4 py-2.5">
                      {stock.is_active ? (
                        <span className="chip border-gain/30 bg-gain/10 text-gain">
                          <CheckCircle2 className="h-3 w-3" /> Active
                        </span>
                      ) : (
                        <span className="chip text-ink-subtle">
                          <XCircle className="h-3 w-3" /> Inactive
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {!loading && visible < filtered.length ? (
          <div className="border-t border-bg-line p-3 text-center">
            <button
              type="button"
              onClick={() => setVisible((current) => current + PAGE_SIZE)}
              className="focus-ring inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg border border-bg-line bg-bg-raised px-4 text-sm font-medium text-ink-muted transition hover:text-ink"
            >
              แสดงเพิ่ม ({(filtered.length - visible).toLocaleString()} ตัวที่เหลือ)
            </button>
          </div>
        ) : null}
      </section>

      <footer className="mt-6 text-center text-[11px] text-ink-subtle">
        รายชื่อหุ้นใน universe ทั้งหมด · ไม่ได้แปลว่าทุกตัวผ่านเกณฑ์คัดกรอง
      </footer>
    </main>
  );
}

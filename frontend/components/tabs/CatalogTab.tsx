"use client";

import { useState } from "react";
import { downloadBlob } from "@/lib/api";

type Catalog = Record<string, string[]>;

export function CatalogTab() {
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleConvert() {
    if (!csvFile) return;
    setLoading(true);
    setError(null);
    setCatalog(null);
    try {
      const fd = new FormData();
      fd.append("csv_file", csvFile);
      const res = await fetch("/api/catalog/from-csv", {
        method: "POST",
        body: fd,
        credentials: "include",
      });
      if (res.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `변환 실패 (${res.status})`);
      }
      setCatalog(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "변환 실패");
    } finally {
      setLoading(false);
    }
  }

  function handleDownload() {
    if (!catalog) return;
    const json = JSON.stringify(catalog, null, 2);
    const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
    downloadBlob(
      new Blob([json], { type: "application/json" }),
      `catalog_${ts}.json`,
    );
  }

  const totalProducts = catalog ? Object.keys(catalog).length : 0;
  const totalOptions = catalog
    ? Object.values(catalog).reduce((s, o) => s + o.length, 0)
    : 0;
  const singleProducts = catalog
    ? Object.values(catalog).filter(
        (o) => o.length === 1 && o[0] === "단일상품",
      ).length
    : 0;

  return (
    <div className="space-y-4">
      <label className="block">
        <div className="mb-2">
          <span className="step-badge">1</span>
          <strong>재고 CSV 업로드</strong>
          <span className="text-xs text-gray-500 ml-2">
            (상품명·옵션내용 컬럼 필요)
          </span>
        </div>
        <input
          type="file"
          accept=".csv"
          onChange={(e) => {
            setCsvFile(e.target.files?.[0] || null);
            setCatalog(null);
          }}
          className="block w-full text-sm border rounded p-2 file:mr-3 file:px-2 file:py-1 file:bg-gray-100 file:border-0 file:rounded"
        />
      </label>

      <button
        onClick={handleConvert}
        disabled={!csvFile || loading}
        className="w-full bg-orange-500 hover:bg-orange-600 text-white rounded p-2 font-bold disabled:opacity-50"
      >
        {loading ? "변환 중..." : "📋 카탈로그 생성"}
      </button>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded text-sm">
          {error}
        </div>
      )}

      {catalog && (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <div className="bg-gray-50 p-3 rounded">
              <div className="text-xs text-gray-500">총 상품 수</div>
              <div className="text-2xl font-bold">{totalProducts}</div>
            </div>
            <div className="bg-gray-50 p-3 rounded">
              <div className="text-xs text-gray-500">총 옵션 수</div>
              <div className="text-2xl font-bold">{totalOptions}</div>
            </div>
            <div className="bg-gray-50 p-3 rounded">
              <div className="text-xs text-gray-500">단일상품</div>
              <div className="text-2xl font-bold">{singleProducts}</div>
            </div>
          </div>
          <div className="border rounded max-h-72 overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  <th className="text-left p-2">#</th>
                  <th className="text-left p-2">상품명</th>
                  <th className="text-left p-2">옵션</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(catalog).map(([p, opts], i) => (
                  <tr key={p} className="border-t">
                    <td className="p-2 text-gray-400">{i + 1}</td>
                    <td className="p-2">{p}</td>
                    <td className="p-2 text-gray-600">{opts.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            onClick={handleDownload}
            className="w-full bg-orange-500 hover:bg-orange-600 text-white rounded p-2 font-bold"
          >
            📥 catalog.json 다운로드
          </button>
        </div>
      )}
    </div>
  );
}

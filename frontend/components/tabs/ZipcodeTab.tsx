"use client";

import { useState } from "react";
import { downloadBlob, filenameFromContentDisposition } from "@/lib/api";

export function ZipcodeTab() {
  const [excelFile, setExcelFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ total: number; found: number } | null>(null);

  async function handleSubmit() {
    if (!excelFile) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("excel_file", excelFile);
      const res = await fetch("/api/zipcode", {
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
        throw new Error(data.detail || `조회 실패 (${res.status})`);
      }
      const blob = await res.blob();
      const filename = filenameFromContentDisposition(
        res.headers.get("content-disposition"),
        "zipcode_result.xlsx",
      );
      downloadBlob(blob, filename);
      setResult({
        total: parseInt(res.headers.get("x-total") || "0", 10),
        found: parseInt(res.headers.get("x-found") || "0", 10),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "조회 실패");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <label className="block">
        <div className="mb-2">
          <span className="step-badge">1</span>
          <strong>주소 엑셀 업로드</strong>
        </div>
        <input
          type="file"
          accept=".xlsx,.xls"
          onChange={(e) => setExcelFile(e.target.files?.[0] || null)}
          className="block w-full text-sm border rounded p-2 file:mr-3 file:px-2 file:py-1 file:bg-gray-100 file:border-0 file:rounded"
        />
        <p className="text-xs text-gray-500 mt-1">
          &apos;주소&apos; 컬럼이 포함된 엑셀 파일을 업로드하세요.
        </p>
      </label>

      <button
        onClick={handleSubmit}
        disabled={!excelFile || loading}
        className="w-full bg-orange-500 hover:bg-orange-600 text-white rounded p-2 font-bold disabled:opacity-50"
      >
        {loading ? "조회 중..." : "📮 우편번호 조회 실행"}
      </button>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded text-sm">
          {error}
        </div>
      )}
      {result && (
        <div className="bg-green-50 border border-green-200 text-green-700 p-3 rounded text-sm">
          🎉 {result.found}/{result.total}건 우편번호 조회 완료. 엑셀 파일을 다운로드했습니다.
        </div>
      )}
    </div>
  );
}

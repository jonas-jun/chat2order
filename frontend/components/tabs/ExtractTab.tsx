"use client";

import { useState } from "react";
import { downloadBlob, filenameFromContentDisposition } from "@/lib/api";

export function ExtractTab() {
  const [catalogFile, setCatalogFile] = useState<File | null>(null);
  const [chatFiles, setChatFiles] = useState<File[]>([]);
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ jobId: string; total: number } | null>(null);

  async function handleSubmit() {
    setError(null);
    setResult(null);
    if (!catalogFile) return setError("카탈로그 파일을 업로드해 주세요.");
    if (!chatFiles.length) return setError("대화 내역 파일을 1개 이상 업로드해 주세요.");
    if (!startTime || !endTime) return setError("시작/종료 시간을 입력해 주세요.");

    setLoading(true);

    const fd = new FormData();
    fd.append("catalog", catalogFile);
    chatFiles.forEach((f) => fd.append("chats", f));
    fd.append("time_after", startTime);
    fd.append("time_before", endTime);

    try {
      const res = await fetch("/api/extract", {
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
        throw new Error(data.detail || `추출 실패 (${res.status})`);
      }
      const blob = await res.blob();
      const filename = filenameFromContentDisposition(
        res.headers.get("content-disposition"),
        "orders_extracted.xlsx",
      );
      downloadBlob(blob, filename);
      setResult({
        jobId: res.headers.get("x-job-id") || "",
        total: parseInt(res.headers.get("x-total-orders") || "0", 10),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "추출 실패");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="block">
          <div className="mb-2">
            <span className="step-badge">1</span>
            <strong>카탈로그 업로드</strong>
          </div>
          <input
            type="file"
            accept=".json"
            onChange={(e) => setCatalogFile(e.target.files?.[0] || null)}
            className="block w-full text-sm border rounded p-2 file:mr-3 file:px-2 file:py-1 file:bg-gray-100 file:border-0 file:rounded"
          />
        </label>
        <label className="block">
          <div className="mb-2">
            <span className="step-badge">2</span>
            <strong>대화 내역 업로드</strong>
          </div>
          <input
            type="file"
            accept=".csv"
            multiple
            onChange={(e) => setChatFiles(Array.from(e.target.files || []))}
            className="block w-full text-sm border rounded p-2 file:mr-3 file:px-2 file:py-1 file:bg-gray-100 file:border-0 file:rounded"
          />
          {chatFiles.length > 0 && (
            <p className="text-xs text-gray-500 mt-1">{chatFiles.length}개 파일 선택됨</p>
          )}
        </label>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="block">
          <div className="mb-2">
            <span className="step-badge">3</span>
            <strong>라이브 시작</strong>
          </div>
          <input
            type="datetime-local"
            value={startTime}
            onChange={(e) => setStartTime(e.target.value)}
            className="block w-full text-sm border rounded p-2"
          />
        </label>
        <label className="block">
          <div className="mb-2">
            <strong>라이브 종료</strong>
          </div>
          <input
            type="datetime-local"
            value={endTime}
            onChange={(e) => setEndTime(e.target.value)}
            className="block w-full text-sm border rounded p-2"
          />
        </label>
      </div>

      <button
        onClick={handleSubmit}
        disabled={loading}
        className="w-full bg-orange-500 hover:bg-orange-600 text-white rounded p-3 font-bold disabled:opacity-50"
      >
        {loading ? "🚀 추출 중... (수 분 소요될 수 있음)" : "🚀 주문서 추출 실행"}
      </button>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded text-sm">
          {error}
        </div>
      )}
      {result && (
        <div className="bg-green-50 border border-green-200 text-green-700 p-3 rounded text-sm">
          🎉 {result.total}건 추출 완료. 엑셀 파일을 다운로드했습니다.
          {result.jobId && (
            <span className="text-gray-500 ml-2">(Job: {result.jobId.slice(0, 8)})</span>
          )}
        </div>
      )}
    </div>
  );
}

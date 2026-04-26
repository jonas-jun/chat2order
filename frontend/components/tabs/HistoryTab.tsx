"use client";

import { useEffect, useState } from "react";
import { apiJson, ApiError } from "@/lib/api";

interface Job {
  id: string;
  title: string;
  live_start_time: string | null;
  total_orders: number;
  model: string;
  created_at: string;
}

export function HistoryTab() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiJson<Job[]>("/api/history")
      .then(setJobs)
      .catch((e) => {
        if (!(e instanceof ApiError) || e.status !== 401) {
          setError(e instanceof Error ? e.message : "이력 로딩 실패");
        }
      });
  }, []);

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded text-sm">
        {error}
      </div>
    );
  }
  if (jobs === null) {
    return <div className="text-gray-500 text-sm">불러오는 중...</div>;
  }
  if (jobs.length === 0) {
    return <div className="text-gray-500 text-sm">저장된 추출 이력이 없습니다.</div>;
  }

  return (
    <div className="space-y-2">
      {jobs.map((job) => (
        <div
          key={job.id}
          className="border rounded p-3 hover:bg-gray-50 transition"
        >
          <div className="flex justify-between items-start gap-3">
            <div className="min-w-0">
              <div className="font-medium truncate">{job.title}</div>
              <div className="text-xs text-gray-500 mt-1">
                라이브:{" "}
                {job.live_start_time?.slice(0, 16).replace("T", " ") || "-"}
                {" | "}
                {job.total_orders}건
                {" | "}
                <span className="text-gray-400">{job.model}</span>
              </div>
            </div>
            <div className="text-xs text-gray-400 whitespace-nowrap">
              {job.created_at?.slice(0, 19).replace("T", " ")}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

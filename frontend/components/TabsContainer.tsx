"use client";

import { useState } from "react";
import { ExtractTab } from "./tabs/ExtractTab";
import { CatalogTab } from "./tabs/CatalogTab";
import { ZipcodeTab } from "./tabs/ZipcodeTab";
import { HistoryTab } from "./tabs/HistoryTab";

type TabId = "extract" | "catalog" | "zipcode" | "history";

const TABS: { id: TabId; label: string; component: () => React.ReactElement }[] = [
  { id: "extract", label: "📦 주문서 추출", component: ExtractTab },
  { id: "catalog", label: "📋 카탈로그 생성", component: CatalogTab },
  { id: "zipcode", label: "📮 우편번호 추출", component: ZipcodeTab },
  { id: "history", label: "🗂️ 나의 추출 이력", component: HistoryTab },
];

export function TabsContainer() {
  const [active, setActive] = useState<TabId>("extract");
  const ActiveComponent =
    TABS.find((t) => t.id === active)?.component ?? ExtractTab;

  return (
    <div>
      <nav className="flex border-b mb-6 gap-1 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActive(t.id)}
            className={`px-4 py-2 text-sm font-medium whitespace-nowrap border-b-2 transition ${
              active === t.id
                ? "border-orange-500 text-orange-600"
                : "border-transparent text-gray-500 hover:text-gray-800"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <ActiveComponent />
    </div>
  );
}

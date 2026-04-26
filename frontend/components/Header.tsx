"use client";

import { useEffect, useState } from "react";

export function Header() {
  const [user, setUser] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setUser(d.user_id))
      .catch(() => undefined);
  }, []);

  async function handleLogout() {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "include",
      });
    } finally {
      window.location.href = "/login";
    }
  }

  return (
    <header className="border-b bg-white">
      <div className="max-w-6xl mx-auto px-6 py-3 flex justify-between items-center">
        <span className="text-sm text-gray-700">
          {user ? (
            <>
              👤 <strong>{user}</strong>님 환영합니다
            </>
          ) : (
            "로딩 중..."
          )}
        </span>
        <button
          onClick={handleLogout}
          className="text-sm px-3 py-1 border rounded hover:bg-gray-50"
        >
          LogOut
        </button>
      </div>
    </header>
  );
}

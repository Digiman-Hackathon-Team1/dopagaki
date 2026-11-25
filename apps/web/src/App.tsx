import { api } from "@repo/api-client";
import { useEffect, useState } from "react";

export function App() {
  const [status, setStatus] = useState("확인 중");

  useEffect(() => {
    api
      .GET("/api/health")
      .then(({ data }) => setStatus(data?.status ?? "오류"))
      .catch(() => setStatus("연결 실패"));
  }, []);

  return (
    <main>
      <h1>Python + TypeScript</h1>
      <p role="status">API 상태: {status}</p>
    </main>
  );
}

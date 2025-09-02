import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function OAuth() {
  const [url, setUrl] = useState<string>(""); // ссылка для старта авторизации
  const [status, setStatus] = useState<string>("");

  useEffect(() => {
    // 1) подтянуть ссылку
    api
      .get("/oauth/url")
      .then((r) => setUrl(r.data.url))
      .catch(() => setStatus("Failed to get OAuth URL"));

    // 2) если в адресной строке уже есть ?code=..., сразу обменять
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    if (code) {
      setStatus("Exchanging code…");
      api
        .post("/oauth/exchange", { code })
        .then(() => setStatus("Authorized ✓"))
        .catch((err) =>
          setStatus(`Exchange failed: ${err?.response?.data?.detail ?? err.message}`)
        );
    }
  }, []);

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-bold">Authorize hh.ru</h1>
      <a className="underline text-blue-600" href={url}>
        Open authorization page
      </a>
      {status && <div className="text-sm opacity-80">{status}</div>}
    </div>
  );
}

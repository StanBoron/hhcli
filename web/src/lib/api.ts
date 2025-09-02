import axios from "axios";
import type { AxiosInstance } from "axios"; // ← type-only (важно при verbatimModuleSyntax)

export const api: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
  headers: { "Content-Type": "application/json" },
  timeout: 30000,
});

api.interceptors.response.use(
  (r) => r,
  (err) => Promise.reject(err)
);

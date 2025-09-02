import axios from "axios";

export const api = axios.create({
  baseURL: "/api", // проксируется на FastAPI через vite.config.ts
  headers: {
    "Content-Type": "application/json",
  },
});

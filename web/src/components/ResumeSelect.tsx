import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

type Resume = { id: string; title?: string };

export default function ResumeSelect(props: {
  value?: string;
  onChange: (v: string) => void;
  className?: string;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["resumes"],
    queryFn: async (): Promise<Resume[]> => {
      const r = await api.get("/resumes");
      const payload = r.data?.data ?? r.data;
      // ожидаем массив резюме
      if (Array.isArray(payload)) return payload;
      // некоторые реализации оборачивают в {items:[]}
      if (payload?.items && Array.isArray(payload.items)) return payload.items;
      return [];
    },
    staleTime: 60_000,
  });

  return (
    <select
      className={props.className ?? "border rounded p-2"}
      value={props.value ?? ""}
      onChange={(e) => props.onChange(e.target.value)}
      disabled={isLoading}
      title={error ? "Failed to load resumes" : "Choose resume"}
    >
      <option value="">Choose resume…</option>
      {(data ?? []).map((r) => (
        <option key={r.id} value={r.id}>
          {r.title || r.id}
        </option>
      ))}
    </select>
  );
}

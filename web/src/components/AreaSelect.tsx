// src/components/AreaSelect.tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useMemo } from "react";

type Area = { id: string; name: string; areas?: Area[] };

function flatten(areas: Area[], level = 0): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  for (const a of areas) {
    out.push({ id: a.id, label: `${"— ".repeat(level)}${a.name}` });
    if (a.areas?.length) out.push(...flatten(a.areas, level + 1));
  }
  return out;
}

export default function AreaSelect(props: { value?: string; onChange: (v: string) => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["dicts", "areas"],
    queryFn: async () => {
      const r = await api.get("/dicts/areas");
      return r.data?.data ?? r.data;
    },
    staleTime: 86400000,
  });

  const flat = useMemo(() => (Array.isArray(data) ? flatten(data) : []), [data]);

  return (
    <select
      className="border rounded p-2"
      value={props.value ?? ""}
      onChange={(e) => props.onChange(e.target.value)}
      disabled={isLoading}
    >
      <option value="">Any region</option>
      {flat.map((a) => (
        <option key={a.id} value={a.id}>
          {a.label}
        </option>
      ))}
    </select>
  );
}

import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

type Value = string | number | boolean | "";

/** двусторонняя синхронизация объекта фильтров и URL-параметров */
export function useQuerySync(filters: Record<string, Value>) {
  const [sp, setSp] = useSearchParams();

  // write → URL
  useEffect(() => {
    const next = new URLSearchParams(sp);
    Object.entries(filters).forEach(([k, v]) => {
      if (v === "" || v === undefined) next.delete(k);
      else next.set(k, String(v));
    });
    const curr = sp.toString();
    const upd = next.toString();
    if (curr !== upd) setSp(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(filters)]);

  // read ← URL (разово в точке вызова читаем sp.get("key"))
  return sp;
}

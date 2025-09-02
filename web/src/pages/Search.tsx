import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { AxiosError } from "axios";
import { api } from "../lib/api";
import AreaSelect from "../components/AreaSelect";
import ResumeSelect from "../components/ResumeSelect";
import { useSettings } from "../store/settings";
import { useLocalStorage } from "../lib/useLocalStorage";
import Skeleton from "../components/Skeleton";
import { useQuerySync } from "../lib/useQuerySync";

type Vacancy = {
  id: string;
  name: string;
  employer?: { name?: string };
  area?: { name?: string };
  salary?: { from?: number; to?: number; currency?: string };
  published_at?: string;
  alternate_url?: string;
};
type SearchResult = { found: number; page: number; pages: number; items: Vacancy[] };

export default function Search() {
  // сохранение фильтров
  const [text, setText] = useLocalStorage<string>("search:text", "");
  const [area, setArea] = useLocalStorage<string>("search:area", "");
  const [page, setPage] = useLocalStorage<number>("search:page", 0);
  const [perPage, setPerPage] = useLocalStorage<number>("search:perPage", 20);
  const [minSalary, setMinSalary] = useLocalStorage<number | "">("search:minSalary", "");
  const [sort, setSort] = useLocalStorage<"date" | "salary_desc">("search:sort", "date");
  const [auto, setAuto] = useLocalStorage<boolean>("search:auto", true);

  // выбор вакансий и параметры массового отклика
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dryRun, setDryRun] = useState<boolean>(true);
  const [skipTested, setSkipTested] = useState<boolean>(true);
  const [requireLetter, setRequireLetter] = useState<boolean>(false);
  const [rateLimit, setRateLimit] = useState<number>(0.5);

  // доступ к резюме/шаблону из стора (используем и в bulk-панели, и в карточках)
  const { resumeId, setResumeId, messageTemplate, setMessageTemplate } = useSettings();

  // синхронизация в URL (ничего не возвращаем, чтобы ESLint не ругался)
  useQuerySync({
    text,
    area,
    page,
    per_page: perPage,
    minSalary: minSalary === "" ? "" : Number(minSalary),
    sort,
    auto,
  });

  // дебаунс для авто-поиска
  const [typing, setTyping] = useState(false);
  useEffect(() => {
    if (!auto) return;
    setTyping(true);
    const t = setTimeout(() => {
      setTyping(false);
      refetch();
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, area, perPage, sort]);

  const payload = useMemo(
    () => ({
      text: text.trim() || undefined,
      area: area || undefined,
      per_page: perPage,
      page,
    }),
    [text, area, perPage, page]
  );

  const {
    data,
    isFetching,
    refetch,
    error: queryError,
  } = useQuery({
    queryKey: ["search", payload],
    queryFn: async (): Promise<SearchResult> => {
      const r = await api.post("/search", payload);
      return r.data?.data ?? r.data;
    },
    enabled: false,
    staleTime: 30_000,
  });

  // первый автозапрос при открытии
  useEffect(() => {
    if (auto) refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const total = data?.found ?? 0;
  const pages = data?.pages ?? 0;
  let items = data?.items ?? [];

  // локальная фильтрация по зарплате
  if (minSalary !== "") {
    items = items.filter((v) => {
      const from = v.salary?.from ?? 0;
      const to = v.salary?.to ?? 0;
      return (from || to) >= Number(minSalary);
    });
  }

  // сортировка
  if (sort === "salary_desc") {
    items.sort((a, b) => (b.salary?.from ?? 0) - (a.salary?.from ?? 0));
  } else {
    items.sort(
      (a, b) => new Date(b.published_at ?? 0).getTime() - new Date(a.published_at ?? 0).getTime()
    );
  }

  const runSearch = () => {
    setPage(0);
    refetch();
  };

  const detailError = (() => {
    const err = queryError as AxiosError<{ detail?: string }> | undefined;
    return err?.response?.data?.detail || err?.message || "";
  })();

  // выбор карточек
  const toggleSelect = (id: string, on?: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      const shouldAdd = on ?? !next.has(id);
      if (shouldAdd) next.add(id);
      else next.delete(id);
      return next;
    });
  };
  const selectPage = (ids: string[], on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
      return next;
    });
  };

  // массовый отклик
  const massRespond = useMutation({
    mutationFn: async (ids: string[]) => {
      const r = await api.post("/respond/mass", {
        ids,
        resume_id: resumeId,
        message: messageTemplate || undefined,
        skip_tested: skipTested,
        require_letter: requireLetter,
        rate_limit: rateLimit,
        limit: undefined,
        dry_run: dryRun,
      });
      return r.data?.data ?? r.data;
    },
  });

  // собрать все ID текущей страницы
  const copyIds = async () => {
    const ids = items.map((v) => v.id).join("\n");
    await navigator.clipboard.writeText(ids);
    alert(`Copied ${items.length} vacancy IDs`);
  };

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-bold">Search vacancies</h1>

      {/* filters */}
      <div className="grid gap-3 md:grid-cols-6">
        <input
          className="border rounded p-2 md:col-span-2"
          placeholder="Query"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <AreaSelect
          value={area}
          onChange={(v) => {
            setArea(v);
            setPage(0);
          }}
        />
        <input
          type="number"
          className="border rounded p-2"
          placeholder="Min salary"
          value={minSalary}
          onChange={(e) => setMinSalary(e.target.value ? Number(e.target.value) : "")}
        />
        <select
          className="border rounded p-2"
          value={sort}
          onChange={(e) => setSort(e.target.value as "date" | "salary_desc")}
        >
          <option value="date">Sort by date</option>
          <option value="salary_desc">Sort by salary ↓</option>
        </select>
        <select
          className="border rounded p-2"
          value={perPage}
          onChange={(e) => {
            setPerPage(Number(e.target.value));
            setPage(0);
          }}
        >
          {[10, 20, 30, 50].map((n) => (
            <option key={n} value={n}>
              {n} / page
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={runSearch}
          className="px-4 py-2 rounded bg-black text-white disabled:opacity-60"
          disabled={isFetching || typing}
        >
          {isFetching || typing ? "Searching…" : "Search"}
        </button>
        <label className="text-sm inline-flex items-center gap-2">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          auto-search on change
        </label>
        <button
          className="px-3 py-1 border rounded"
          onClick={copyIds}
          disabled={items.length === 0}
        >
          Copy page IDs
        </button>
        {total > 0 && <div className="text-sm opacity-80">{total} found</div>}
        {detailError && <div className="text-sm text-red-600">error: {detailError}</div>}
      </div>

      {/* bulk toolbar */}
      <div className="flex items-center gap-3 flex-wrap border rounded p-2">
        <div className="text-sm">
          Selected: <b>{selected.size}</b>
          {items.length > 0 && (
            <>
              {" "}
              / page{" "}
              <button
                className="underline"
                onClick={() =>
                  selectPage(
                    items.map((i) => i.id),
                    true
                  )
                }
              >
                select all
              </button>
              {" / "}
              <button
                className="underline"
                onClick={() =>
                  selectPage(
                    items.map((i) => i.id),
                    false
                  )
                }
              >
                clear page
              </button>
            </>
          )}
        </div>

        <ResumeSelect className="border rounded p-1" value={resumeId} onChange={setResumeId} />
        <input
          className="border rounded p-1 md:w-80"
          placeholder="message (optional template)"
          value={messageTemplate}
          onChange={(e) => setMessageTemplate(e.target.value)}
        />

        <label className="text-sm inline-flex items-center gap-1">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />{" "}
          dry-run
        </label>
        <label className="text-sm inline-flex items-center gap-1">
          <input
            type="checkbox"
            checked={skipTested}
            onChange={(e) => setSkipTested(e.target.checked)}
          />{" "}
          skip tested
        </label>
        <label className="text-sm inline-flex items-center gap-1">
          <input
            type="checkbox"
            checked={requireLetter}
            onChange={(e) => setRequireLetter(e.target.checked)}
          />{" "}
          require letter
        </label>
        <label className="text-sm inline-flex items-center gap-1">
          rate:{" "}
          <input
            type="number"
            step="0.1"
            min="0.1"
            className="border rounded p-1 w-20"
            value={rateLimit}
            onChange={(e) => setRateLimit(Number(e.target.value) || 0.5)}
          />
        </label>

        <button
          className="px-3 py-1 rounded bg-black text-white disabled:opacity-50"
          disabled={!resumeId || selected.size === 0 || massRespond.isPending}
          onClick={() => massRespond.mutate(Array.from(selected))}
        >
          {massRespond.isPending ? "Responding…" : "Respond selected"}
        </button>

        {massRespond.isError && (
          <div className="text-xs text-red-600">
            {(massRespond.error as AxiosError<{ detail?: string }>)?.response?.data?.detail ??
              "Mass respond failed"}
          </div>
        )}
        {massRespond.isSuccess && (
          <div className="text-xs text-green-700">Done{dryRun ? " (dry-run)" : ""}.</div>
        )}
      </div>

      {/* results */}
      <div className="space-y-3">
        {isFetching && items.length === 0 && (
          <div className="space-y-2">
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </div>
        )}
        {!isFetching &&
          items.map((v) => (
            <VacancyCard key={v.id} v={v} selected={selected.has(v.id)} onToggle={toggleSelect} />
          ))}
        {items.length === 0 && !isFetching && !detailError && (
          <div className="text-sm opacity-70">No results — change filters or query.</div>
        )}
      </div>

      {/* pagination */}
      {pages > 1 && (
        <div className="flex items-center gap-2 pt-2 flex-wrap">
          <button
            className="px-3 py-1 border rounded disabled:opacity-50"
            onClick={() => setPage(0)}
            disabled={page === 0 || isFetching}
          >
            « First
          </button>
          <button
            className="px-3 py-1 border rounded disabled:opacity-50"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0 || isFetching}
          >
            ‹ Prev
          </button>
          <div className="text-sm">
            Page {page + 1} / {pages}
          </div>
          <button
            className="px-3 py-1 border rounded disabled:opacity-50"
            onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
            disabled={page >= pages - 1 || isFetching}
          >
            Next ›
          </button>
          <button
            className="px-3 py-1 border rounded disabled:opacity-50"
            onClick={() => setPage(pages - 1)}
            disabled={page >= pages - 1 || isFetching}
          >
            Last »
          </button>
        </div>
      )}
    </div>
  );
}

function VacancyCard({
  v,
  selected,
  onToggle,
}: {
  v: Vacancy;
  selected: boolean;
  onToggle: (id: string, on?: boolean) => void;
}) {
  const link = v.alternate_url ?? `https://hh.ru/vacancy/${v.id}`;
  const published = v.published_at ? new Date(v.published_at).toLocaleString() : "—";
  const employer = v.employer?.name ?? "—";
  const area = v.area?.name ?? "—";
  const salary = v.salary
    ? [v.salary.from, v.salary.to].filter(Boolean).join("–") +
      (v.salary.currency ? ` ${v.salary.currency}` : "")
    : "—";

  const { resumeId, messageTemplate, setResumeId, setMessageTemplate } = useSettings();

  const canRespond = useMutation({
    mutationFn: async () => {
      const r = await api.post("/can-respond", null, {
        params: { vacancy_id: v.id, resume_id: resumeId },
      });
      return r.data?.data ?? r.data;
    },
  });

  const respond = useMutation({
    mutationFn: async () => {
      const r = await api.post("/respond", {
        vacancy_id: v.id,
        resume_id: resumeId,
        message: messageTemplate || undefined,
      });
      return r.data?.data ?? r.data;
    },
  });

  const errDetail = (() => {
    const err = (canRespond.error ?? respond.error) as AxiosError<{ detail?: string }> | undefined;
    return err?.response?.data?.detail || "Request failed";
  })();

  return (
    <div
      className={"border rounded p-3 transition " + (selected ? "bg-blue-50" : "hover:bg-gray-50")}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={selected}
            onChange={(e) => onToggle(v.id, e.target.checked)}
          />
          <a
            className="text-lg font-semibold underline"
            href={link}
            target="_blank"
            rel="noreferrer"
          >
            {v.name}
          </a>
        </div>
        <span className="text-xs opacity-70">{published}</span>
      </div>

      <div className="text-sm opacity-80 mt-1">
        {employer} • {area}
      </div>
      <div className="text-sm mt-1">salary: {salary}</div>

      <div className="mt-3 grid gap-2 md:grid-cols-3">
        <ResumeSelect className="border rounded p-2" value={resumeId} onChange={setResumeId} />
        <input
          className="border rounded p-2 md:col-span-2"
          placeholder="message (optional template)"
          value={messageTemplate}
          onChange={(e) => setMessageTemplate(e.target.value)}
        />
      </div>

      <div className="mt-2 flex items-center gap-2">
        <button
          className="px-3 py-1 border rounded disabled:opacity-50"
          onClick={() => canRespond.mutate()}
          disabled={!resumeId || canRespond.isPending}
        >
          {canRespond.isPending ? "Checking…" : "Can respond?"}
        </button>
        <button
          className="px-3 py-1 border rounded bg-black text-white disabled:opacity-50"
          onClick={() => respond.mutate()}
          disabled={!resumeId || respond.isPending}
        >
          {respond.isPending ? "Responding…" : "Respond"}
        </button>
        {canRespond.data !== undefined && (
          <span className={"text-sm " + (canRespond.data ? "text-green-600" : "text-red-600")}>
            {String(canRespond.data)}
          </span>
        )}
      </div>

      {(canRespond.isError || respond.isError) && (
        <div className="text-xs text-red-600 mt-1">{errDetail}</div>
      )}
    </div>
  );
}

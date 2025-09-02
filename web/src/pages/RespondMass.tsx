import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { AxiosError } from "axios";
import { api } from "../lib/api";
import ResumeSelect from "../components/ResumeSelect";
import { useSettings } from "../store/settings";
import { useEffect } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

export default function RespondMass() {
  const [idsText, setIdsText] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [skipTested, setSkipTested] = useState(true);
  const [requireLetter, setRequireLetter] = useState(false);
  const [rateLimit, setRateLimit] = useState(0.5);
  const [limit, setLimit] = useState<number | "">("");
  const location = useLocation();
  const [sp] = useSearchParams();

  const { resumeId, setResumeId, messageTemplate, setMessageTemplate } = useSettings();

  useEffect(() => {
    const fromState = (location.state as Partial<{ ids: string[] }>)?.ids;
    const fromQuery = sp
      .get("ids")
      ?.split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const ids = fromState?.length ? fromState : fromQuery?.length ? fromQuery : [];
    if (ids.length) setIdsText(ids.join("\n"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const massRespond = useMutation({
    mutationFn: async () => {
      const ids = idsText
        .split(/[\s,;]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      const r = await api.post("/respond/mass", {
        ids,
        resume_id: resumeId,
        message: messageTemplate || undefined,
        skip_tested: skipTested,
        require_letter: requireLetter,
        rate_limit: rateLimit,
        limit: limit === "" ? undefined : Number(limit),
        dry_run: dryRun,
      });
      return r.data?.data ?? r.data;
    },
  });

  const errDetail =
    (massRespond.error as AxiosError<{ detail?: string }> | undefined)?.response?.data?.detail ||
    (massRespond.error as Error | undefined)?.message ||
    "";

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-bold">Mass respond</h1>

      <div className="grid gap-3 md:grid-cols-2">
        <textarea
          className="border rounded p-2 h-60"
          placeholder="Paste vacancy IDs (one per line or separated by spaces/commas)…"
          value={idsText}
          onChange={(e) => setIdsText(e.target.value)}
        />
        <div className="space-y-3">
          <ResumeSelect
            className="border rounded p-2 w-full"
            value={resumeId}
            onChange={setResumeId}
          />
          <input
            className="border rounded p-2 w-full"
            placeholder="message (optional template)"
            value={messageTemplate}
            onChange={(e) => setMessageTemplate(e.target.value)}
          />
          <div className="flex items-center gap-4 flex-wrap">
            <label className="text-sm inline-flex items-center gap-1">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(e) => setDryRun(e.target.checked)}
              />{" "}
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
          </div>
          <div className="flex items-center gap-3">
            <label className="text-sm inline-flex items-center gap-1">
              rate:
              <input
                type="number"
                step="0.1"
                min="0.1"
                className="border rounded p-1 w-24 ml-1"
                value={rateLimit}
                onChange={(e) => setRateLimit(Number(e.target.value) || 0.5)}
              />
            </label>
            <label className="text-sm inline-flex items-center gap-1">
              limit:
              <input
                type="number"
                min="1"
                className="border rounded p-1 w-24 ml-1"
                value={limit}
                onChange={(e) => setLimit(e.target.value === "" ? "" : Number(e.target.value))}
              />
            </label>
          </div>
          <button
            className="px-4 py-2 rounded bg-black text-white disabled:opacity-50"
            disabled={!resumeId || !idsText.trim() || massRespond.isPending}
            onClick={() => massRespond.mutate()}
          >
            {massRespond.isPending ? "Responding…" : "Run mass respond"}
          </button>
          {massRespond.isError && <div className="text-sm text-red-600">Error: {errDetail}</div>}
          {massRespond.isSuccess && (
            <pre className="text-xs bg-gray-50 border rounded p-2 overflow-auto max-h-60">
              {JSON.stringify(massRespond.data, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

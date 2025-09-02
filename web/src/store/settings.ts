import { create } from "zustand";

type Settings = {
  resumeId: string;
  messageTemplate: string;
  setResumeId: (v: string) => void;
  setMessageTemplate: (v: string) => void;
};

const STORAGE_KEY = "hhcli-settings";

function load(): Partial<Pick<Settings, "resumeId" | "messageTemplate">> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function save(data: Pick<Settings, "resumeId" | "messageTemplate">) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

export const useSettings = create<Settings>((set, get) => {
  const initial = load();
  return {
    resumeId: initial.resumeId ?? "",
    messageTemplate: initial.messageTemplate ?? "",
    setResumeId: (resumeId) => {
      set({ resumeId });
      save({ resumeId, messageTemplate: get().messageTemplate });
    },
    setMessageTemplate: (messageTemplate) => {
      set({ messageTemplate });
      save({ resumeId: get().resumeId, messageTemplate });
    },
  };
});

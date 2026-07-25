import { create } from "zustand";
import type { Lead } from "../types/lead";

type Tab = "leads" | "framework" | "archive" | "feedback";

interface AppState {
  leads: Lead[];
  setLeads: (leads: Lead[]) => void;
  updateLead: (updated: Lead) => void;
  activeTab: Tab;
  setActiveTab: (tab: Tab) => void;
  // Admin "view as" QA mode (issue #52): non-null while an admin is viewing a
  // teammate's board read-only. Read by the request interceptor
  // (client.ts) to append ?view_as= on every request.
  viewAs: string | null;
  setViewAs: (email: string | null) => void;
}

const useAppStore = create<AppState>((set) => ({
  leads: [],
  setLeads: (leads) => set({ leads }),
  updateLead: (updated) =>
    set((state) => ({
      leads: state.leads.map((l) => (l.id === updated.id ? updated : l)),
    })),
  activeTab: "leads",
  setActiveTab: (tab) => set({ activeTab: tab }),
  viewAs: null,
  setViewAs: (viewAs) => set({ viewAs }),
}));

export default useAppStore;

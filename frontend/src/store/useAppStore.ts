import { create } from "zustand";
import type { Lead } from "../types/lead";

type Tab = "leads" | "briefing" | "framework" | "sendqueue" | "archive" | "feedback" | "portfolio";

interface AppState {
  leads: Lead[];
  setLeads: (leads: Lead[]) => void;
  updateLead: (updated: Lead) => void;
  activeTab: Tab;
  setActiveTab: (tab: Tab) => void;
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
}));

export default useAppStore;

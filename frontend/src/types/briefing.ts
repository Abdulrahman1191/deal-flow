export interface Theme {
  rank: number;
  title: string;
  description: string;
  tags: string[];
  sources: string[];
}

export interface DeepDive {
  title: string;
  body: string;
  sources: string[];
}

export interface Briefing {
  id: string;
  date: string;
  top_themes: Theme[];
  deep_dives: DeepDive[];
  generated_at: string;
}

export interface PaginatedBriefings {
  total: number;
  page: number;
  page_size: number;
  items: Briefing[];
}

export interface Vacancy {
  id: string;
  name: string;
  employer?: { name?: string; logo_urls?: Record<string, string> };
  area?: { name?: string };
  salary?: { from?: number; to?: number; currency?: string; gross?: boolean };
  published_at?: string;
  alternate_url?: string;
}

export interface SearchResult {
  found: number;
  page: number;
  pages: number;
  items: Vacancy[];
}

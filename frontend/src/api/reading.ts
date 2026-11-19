import { apiRequest } from './client';
import type { ExamVariant } from './types';

export type ReadingSectionType = 'tfng' | 'matching_headings' | 'multiple_choice';
export type ReadingOption = { id: string; text: string };
export type ReadingItem = { id: string; number: number; prompt: string; options: ReadingOption[] };
export type ReadingSection = { type: ReadingSectionType; instructions: string; items: ReadingItem[] };
type ArticleInformation = {
  id: string; title: string; theme: string; summary: string; difficulty: number; question_count: number; word_count: number;
};
export type ReadingArticleSummary = ArticleInformation & {
  status: 'not_started' | 'completed'; correct_count: number | null;
};
export type ReadingArticle = ArticleInformation & {
  paragraphs: { id: string; text: string }[]; sections: ReadingSection[]; source: string;
};
export type ReadingItemResult = {
  item_id: string; number: number; selected_option: string; correct_option: string; is_correct: boolean;
  explanation: string; attempt_id: string;
  evidence: { material_id: string; content_version: string; paragraph: string; quote: string };
};
export type ReadingResult = {
  session_id: string; content_version: string; rule_version: string; submitted_at: string;
  correct_count: number; total_count: number; items: ReadingItemResult[];
};
export type ReadingArticleView = { article: ReadingArticle; result: ReadingResult | null };
export type ReadingCatalog = {
  status: 'selection_required' | 'empty' | 'unimported' | 'ready'; exam_variant: ExamVariant | null;
  articles: ReadingArticleSummary[]; missing_count: number;
};

const root = '/api/v1/reading/articles';
const scope = (learner: string) => `?learner_id=${encodeURIComponent(learner)}`;
const articlePath = (id: string) => `${root}/${encodeURIComponent(id)}`;
export const readingApi = {
  list: (learner: string, signal?: AbortSignal) => apiRequest<ReadingCatalog>(root + scope(learner), { signal }),
  get: (id: string, learner: string, signal?: AbortSignal) =>
    apiRequest<ReadingArticleView>(articlePath(id) + scope(learner), { signal }),
  submit: (id: string, learner: string, answers: Record<string, string>) =>
    apiRequest<ReadingArticleView>(articlePath(id) + '/submit' + scope(learner), {
      method: 'POST', body: JSON.stringify({ answers }),
    }),
};

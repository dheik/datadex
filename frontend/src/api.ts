import { Platform } from 'react-native';

// No emulador Android, "localhost" é o próprio emulador: o PC fica em 10.0.2.2.
const DEFAULT_URL = Platform.OS === 'android' ? 'http://10.0.2.2:8000' : 'http://localhost:8000';
export const API_URL = process.env.EXPO_PUBLIC_API_URL || DEFAULT_URL;

export type Pokemon = {
  id: number;
  pokedex_number: number;
  name: string;
  species: string;
  form: string | null;
  form_category: 'base' | 'regional' | 'mega' | 'gigantamax' | 'alternate';
  types: string[];
  hp: number; attack: number; defense: number; sp_attack: number; sp_defense: number; speed: number;
  total: number;
  generation: number;
  region: string;
  height_m: number;
  weight_kg: number;
  is_legendary: boolean;
  is_mythical: boolean;
  is_baby: boolean;
  capture_rate: number | null;
  sprite_url: string;
  abilities: { name: string; hidden: boolean }[];
  query_values?: Record<string, unknown>;
};

// Uma chamada de função feita pelo Gemini (function calling)
export type ToolCall = {
  name: string;
  source: 'Banco local' | 'PokéAPI' | 'TCGdex' | string;
  args: Record<string, unknown>;
  ok: boolean;
  summary: string;
  ms: number;
};

export type EvolutionStage = {
  species: string;
  name: string;
  id: number | null;
  sprite_url: string | null;
  types: string[];
  from: string | null;
  condition: string;
  stage: number;
};

export type Weaknesses = {
  pokemon: string | null;
  types: string[];
  weak: Record<string, number>;
  resist: Record<string, number>;
  immune: string[];
};

export type TcgCards = {
  pokemon: string;
  total: number;
  cards: { id: string; nome: string; numero: string; imagem: string; imagem_grande: string }[];
};

export type PokedexDescription = {
  pokemon: string;
  id: number;
  genus: string | null;
  text: string | null;
  language: string;
  version: string | null;
};

export type AskResponse = {
  id: string;
  question: string;
  answer: string;
  explanation: string;
  sql: string | null;
  result_type: 'pokemon' | 'table' | 'info';
  pokemon: Pokemon[];
  table: { columns: string[]; rows: Record<string, unknown>[] } | null;
  description: PokedexDescription | null;
  evolution: EvolutionStage[] | null;
  weaknesses: Weaknesses | null;
  cards: TcgCards | null;
  tool_calls: ToolCall[];
  row_count: number;
  truncated: boolean;
  model: string | null;
  cached: boolean;
  elapsed_ms: number;
};

export class ApiError extends Error {
  constructor(message: string, public code: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    });
  } catch {
    throw new ApiError(`Sem conexão com a API em ${API_URL}. Verifique se o backend está rodando.`, 'network');
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(body?.error?.message || `Erro ${res.status} na API.`, body?.error?.code || 'http');
  }
  return body as T;
}

export const api = {
  ask: (question: string, sessionId: string) =>
    request<AskResponse>('/api/ask', { method: 'POST', body: JSON.stringify({ question, session_id: sessionId }) }),
  examples: () => request<string[]>('/api/examples'),
};

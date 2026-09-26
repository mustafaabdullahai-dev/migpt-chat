export type Mode = "auto" | "web" | "deep" | "no_web";

export interface ResearchPhase {
  id: "searching" | "reading" | "synthesizing";
  label: string;
  status: "active" | "done";
  count?: number;
}

export interface Source {
  source_id: string;
  title: string;
  url: string;
  snippet: string;
  category?: string;
  quality?: string;
  citation_index?: number;
  relevance?: number;
}

export interface ToolRun {
  tool: string;
  arguments: string;
  started_at: string;
  ended_at?: string;
  duration_ms?: number;
  status?: "running" | "success" | "error";
  result?: string;
  error?: string;
}

export interface ChatMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  intent?: string;
  confidence?: number;
  tool_runs: ToolRun[];
  followups?: string[];
  attachments?: Attachment[];
  created_at: string;
  is_streaming?: boolean;
}

export interface Conversation {
  conversation_id: string;
  title: string;
  is_pinned?: boolean;
  created_at: string;
  updated_at: string;
  metadata?: Record<string, unknown>;
  summary?: string;
  message_count?: number;
  last_message?: string;
}

export interface ConversationSearchHit {
  conversation: Conversation;
  message_id?: string;
  snippet_role?: string;
  snippet?: string;
}

export interface Health {
  status: string;
  version: string;
  llm_provider: string;
  model_name: string;
  search_provider: string;
  calculator: boolean;
  tools: string[];
  tool_count: number;
}

export interface DocSummary {
  document_id: string;
  filename: string;
  chunks: number;
  created_at: string;
}

export interface Attachment {
  document_id: string;
  filename: string;
}

export interface FeedbackPayload {
  conversation_id: string;
  message_id: string;
  rating: "up" | "down";
}
export type Role = "admin" | "coder" | "auditor" | "service";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
}

export interface DocumentSummary {
  id: string;
  external_id: string | null;
  filename: string;
  mime_type: string;
  size_bytes: number;
  source: string;
  status: string;
  review_route: string | null;
  encounter_type: string;
  page_count: number | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  finalized_at: string | null;
}

export interface Entity {
  id: string;
  text: string;
  normalized: string | null;
  category: string;
  start: number;
  end: number;
  section: string | null;
  negated: boolean;
  uncertain: boolean;
  historical: boolean;
  family: boolean;
  laterality: string | null;
  severity: string | null;
  temporal: string | null;
  source: string;
  assertion_conflict: boolean;
}

export interface FinalCode {
  code_system: string;
  code: string;
  description: string;
  sequence: number;
  ai_suggested: boolean;
  ai_modified: boolean;
}

export interface DocumentDetail extends DocumentSummary {
  text: string | null;
  patient_sex: string | null;
  patient_age: number | null;
  extraction_method: string | null;
  sections: { name: string; header: string | null; start: number; end: number }[];
  entities: Entity[];
  final_codes: FinalCode[];
}

export interface Issue {
  rule: string;
  severity: "reject" | "flag" | "info";
  message: string;
  category: string | null;
  related_codes: string[];
}

export interface Suggestion {
  id: string;
  document_id: string;
  code_system: string;
  code: string;
  description: string;
  kb_version: string | null;
  entity_text: string | null;
  rationale: string | null;
  sequence: number;
  source: string;
  llm_confidence: number | null;
  confidence: number;
  confidence_breakdown: {
    signals?: Record<string, number | null>;
    threshold?: number;
    calibration?: string;
  };
  retrieval_score: number | null;
  retrieval_rank: number | null;
  validation_status: "passed" | "flagged" | "rejected";
  validation_issues: Issue[];
  review_route: "standard" | "mandatory" | "system_rejected";
  status: "pending_review" | "approved" | "rejected" | "edited" | "superseded";
  final_code: string | null;
  final_description: string | null;
  evidence: { quote: string; start: number | null; end: number | null; match_score: number }[];
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor_type: string;
  actor_label: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  document_id: string | null;
  details: Record<string, unknown>;
  hash: string;
}

export interface ModelRun {
  id: string;
  run_type: string;
  provider: string;
  model: string;
  prompt_version: string | null;
  retrieval_version: string | null;
  kb_version: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  status: string;
  created_at: string;
  config: Record<string, unknown>;
}

export interface Stats {
  documents_by_status: Record<string, number>;
  pending_review: number;
  mandatory_review: number;
  suggestions_by_status: Record<string, number>;
  reviews_by_action: Record<string, number>;
  acceptance_rate: number | null;
  correction_rate: number | null;
  ai_precision: number | null;
  error_categories: Record<string, number>;
  avg_confidence_approved: number | null;
  avg_confidence_rejected: number | null;
  llm_cost_usd_total: number;
  avg_coding_latency_ms: number | null;
}

export const ERROR_CATEGORIES = [
  "wrong_code",
  "missing_code",
  "extra_code",
  "wrong_specificity",
  "negation_error",
  "wrong_procedure",
  "wrong_terminology",
  "retrieval_failure",
  "reasoning_failure",
  "validation_failure",
  "other",
] as const;

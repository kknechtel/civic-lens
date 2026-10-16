/**
 * Meetings API client — wraps /api/meetings.
 *
 * A "meeting" is a Document with doc_type starting with `recording_`. The
 * detail endpoint adds a presigned audio_url (for town audio) or a
 * youtube_id (for HHRSD videos), plus the stored transcript + summary.
 */
import { getAuthToken } from "./api";

const API_BASE = typeof window !== "undefined" ? "" : (process.env.NEXT_PUBLIC_API_URL || "");

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
    ...getAuthToken(),
  };
  if (!(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export interface MeetingListItem {
  id: string;
  title: string;
  meeting_body: string;
  meeting_date: string | null;
  platform: "audio" | "youtube";
  doc_type: string;
  category: string | null;
  status: string;
  has_transcript: boolean;
  has_summary: boolean;
  duration_seconds: number | null;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
}

export interface Transcript {
  engine: string;
  language: string;
  duration_seconds: number;
  segments: TranscriptSegment[];
  warnings?: string[];
}

export interface MeetingSummary {
  tldr: string;
  decisions: { description: string; vote: string; timestamp_seconds: number }[];
  action_items: { description: string; owner: string; due: string | null }[];
  topics: { title: string; summary: string; start_seconds: number; end_seconds: number }[];
  public_comments: { speaker: string; topic: string; timestamp_seconds: number }[];
  ordinances_resolutions: { number: string; description: string; outcome: string }[];
}

export interface MeetingDetail extends MeetingListItem {
  youtube_id: string | null;
  audio_url: string | null;
  transcript: Transcript | null;
  summary: MeetingSummary | null;
}

export async function listMeetings(opts: { body?: string; platform?: string } = {}): Promise<MeetingListItem[]> {
  const params = new URLSearchParams();
  if (opts.body) params.set("body", opts.body);
  if (opts.platform) params.set("platform", opts.platform);
  const qs = params.toString();
  return req<MeetingListItem[]>(`/api/meetings/${qs ? `?${qs}` : ""}`);
}

export async function getMeeting(id: string): Promise<MeetingDetail> {
  return req<MeetingDetail>(`/api/meetings/${id}`);
}

export async function transcribeMeeting(id: string, force = false) {
  return req<{ status: string; id: string }>(`/api/meetings/${id}/transcribe?force=${force}`, { method: "POST" });
}

export async function summarizeMeeting(id: string) {
  return req<{ status: string; id: string }>(`/api/meetings/${id}/summarize`, { method: "POST" });
}
